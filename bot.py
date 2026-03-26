import discord
from discord.ext import commands
import asyncio
import io
import os
import re
import time
import json
import socket
import struct
from datetime import datetime, timezone
from collections import defaultdict
from pathlib import Path

intents = discord.Intents.all()
bot = commands.Bot(command_prefix="!", intents=intents)

# ─────────────────────────────────────────────
#  CONSTANTES GÉNÉRALES
# ─────────────────────────────────────────────
ROLE_ID           = 913064374590140417
CATEGORY_ID       = 1419109736091095090
ROLE_AUTORISE     = 703339900929441803
LOG_CHANNEL_ID    = 713166766229946418
ROSTER_CHANNEL_ID = 840695680288423976

ROSTER_ROLES = [
    (706808147796426783, "👑 Leader"),
    (703344242017173524, "⚔️ Officier"),
    (703339574515990549, "🛡️ Membre de confiance"),
    (722074234611826809, "⭐ Membre +"),
    (703339648591855656, "🔹 Membre"),
    (739879603497336928, "🌱 Recrue"),
]

STAFF_ROLE_IDS       = {706808147796426783, 703344242017173524}
ALLOWED_DOMAINS      = {"tenor.com", "giphy.com"}
ALLOWED_CMD_CHANNELS = {703342923634180137, 703349716183941162}

SPAM_LIMIT  = 4
SPAM_WINDOW = 6.0
spam_tracker: dict[int, list[float]] = defaultdict(list)
spam_warned:  set[int] = set()

# ─────────────────────────────────────────────
#  CONSTANTES TRACKING
# ─────────────────────────────────────────────
TRACKING_FILE    = "tracking_data.json"
active_trackers: dict[str, dict] = {}  # key → {msg_id, channel_id}


# ═══════════════════════════════════════════════════════════════
#  UTILITAIRES GÉNÉRAUX
# ═══════════════════════════════════════════════════════════════
def is_staff(member: discord.Member) -> bool:
    if member.guild_permissions.administrator:
        return True
    return any(r.id in STAFF_ROLE_IDS for r in member.roles)


async def get_log_channel(guild: discord.Guild):
    try:
        return guild.get_channel(LOG_CHANNEL_ID) or await guild.fetch_channel(LOG_CHANNEL_ID)
    except Exception:
        return None


async def send_log(guild: discord.Guild, embed: discord.Embed):
    ch = await get_log_channel(guild)
    if ch:
        try:
            await ch.send(embed=embed)
        except Exception as e:
            print(f"[LOG] Erreur : {e}")


def now_str() -> str:
    return discord.utils.format_dt(datetime.now(timezone.utc), style="F")


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


# ─────────────────────────────────────────────
#  CHECK GLOBAL : salon autorisé pour commandes
# ─────────────────────────────────────────────
@bot.check
async def check_command_channel(ctx: commands.Context) -> bool:
    if is_staff(ctx.author):
        return True
    if ctx.channel.id not in ALLOWED_CMD_CHANNELS:
        channels = " ou ".join(f"<#{cid}>" for cid in ALLOWED_CMD_CHANNELS)
        await ctx.send(
            f"❌ {ctx.author.mention} Tu ne peux pas utiliser des commandes dans ce salon.\n"
            f"➡️ Rends-toi dans {channels}",
            delete_after=8
        )
        return False
    return True


# ═══════════════════════════════════════════════════════════════
#  TRANSCRIPT HTML
# ═══════════════════════════════════════════════════════════════
async def generate_transcript(channel: discord.TextChannel) -> str:
    messages = []
    async for msg in channel.history(limit=None, oldest_first=True):
        ts      = msg.created_at.strftime("%d/%m/%Y %H:%M:%S")
        author  = discord.utils.escape_markdown(str(msg.author))
        content = msg.content.replace("<", "&lt;").replace(">", "&gt;") or "<em>embed/fichier</em>"
        messages.append(f'<tr><td class="ts">{ts}</td><td class="author">{author}</td><td>{content}</td></tr>')
    rows = "\n".join(messages)
    return f"""<!DOCTYPE html><html lang="fr"><head><meta charset="UTF-8">
<title>Transcript – {channel.name}</title><style>
body{{font-family:Arial,sans-serif;background:#1e1e2e;color:#cdd6f4;padding:20px}}
h1{{color:#cba6f7}}table{{width:100%;border-collapse:collapse;margin-top:16px}}
th{{background:#313244;color:#89b4fa;padding:8px 12px;text-align:left}}
td{{padding:6px 12px;border-bottom:1px solid #313244;vertical-align:top}}
.ts{{color:#a6adc8;white-space:nowrap;width:160px}}.author{{color:#f38ba8;white-space:nowrap;width:180px}}
</style></head><body>
<h1>📄 Transcript – #{channel.name}</h1>
<p>Généré le {now_utc().strftime("%d/%m/%Y à %H:%M UTC")}</p>
<table><thead><tr><th>Horodatage</th><th>Auteur</th><th>Message</th></tr></thead>
<tbody>{rows}</tbody></table></body></html>"""


async def send_ticket_log(guild, ticket_channel, closer):
    ch = await get_log_channel(guild)
    if not ch:
        return
    html = await generate_transcript(ticket_channel)
    file = discord.File(fp=io.BytesIO(html.encode("utf-8")), filename=f"transcript-{ticket_channel.name}.html")
    embed = discord.Embed(title="📁 Ticket fermé", color=0x9B59B6, timestamp=now_utc())
    embed.add_field(name="🎫 Ticket",    value=ticket_channel.name, inline=True)
    embed.add_field(name="👤 Fermé par", value=closer.mention,      inline=True)
    embed.add_field(name="🕐 Date",      value=now_str(),            inline=True)
    embed.set_footer(text=f"ID : {ticket_channel.id}")
    try:
        await ch.send(embed=embed, file=file)
    except Exception as e:
        print(f"[LOG] Erreur ticket : {e}")


# ═══════════════════════════════════════════════════════════════
#  ROSTER
# ═══════════════════════════════════════════════════════════════
def build_roster_embed(guild: discord.Guild) -> discord.Embed:
    role_ids_ordered = [r[0] for r in ROSTER_ROLES]
    categories: dict[int, list[str]] = {rid: [] for rid, _ in ROSTER_ROLES}
    for member in guild.members:
        if member.bot:
            continue
        member_role_ids = {r.id for r in member.roles}
        for rid in role_ids_ordered:
            if rid in member_role_ids:
                categories[rid].append(member.mention)
                break
    embed = discord.Embed(title="📋 Roster — La Mystic", color=0x9B59B6, timestamp=now_utc())
    total = 0
    for rid, label in ROSTER_ROLES:
        members = categories[rid]
        total += len(members)
        if members:
            embed.add_field(name=f"{label} ({len(members)})", value="\n".join(members), inline=False)
    embed.set_footer(text=f"Total : {total} membres")
    return embed


# ═══════════════════════════════════════════════════════════════
#  VUES TICKETS
# ═══════════════════════════════════════════════════════════════
class TicketView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="📋 Demande de recrutement", style=discord.ButtonStyle.green)
    async def recrutement(self, interaction: discord.Interaction, button: discord.ui.Button):
        await creer_ticket(interaction, "recrutement")

    @discord.ui.button(label="📩 Autre demande", style=discord.ButtonStyle.blurple)
    async def autre(self, interaction: discord.Interaction, button: discord.ui.Button):
        await creer_ticket(interaction, "autre")


class FermerView(discord.ui.View):
    def __init__(self, closer: discord.Member):
        super().__init__(timeout=30)
        self.closer = closer
        self.action_taken = False
        self._msg = None

    async def update_countdown(self, message: discord.Message):
        self._msg = message
        for remaining in range(29, 0, -1):
            if self.action_taken:
                return
            await asyncio.sleep(1)
            try:
                embed = discord.Embed(
                    title="🔒 Fermer le ticket",
                    description=f"Es-tu sûr ?\n\n⏳ Expiration dans **{remaining}s**…",
                    color=0xFF0000
                )
                embed.set_footer(text="Aucune action = ticket conservé")
                await message.edit(embed=embed)
            except (discord.NotFound, discord.HTTPException):
                return

    async def on_timeout(self):
        if self.action_taken:
            return
        self.action_taken = True
        self._disable_all()
        if self._msg:
            embed = discord.Embed(title="⏳ Temps écoulé", description="Le ticket n'a **pas** été fermé.", color=0xE67E22)
            try:
                await self._msg.edit(embed=embed, view=self)
            except Exception:
                pass

    def _disable_all(self):
        for child in self.children:
            child.disabled = True

    @discord.ui.button(label="✅ Confirmer la fermeture", style=discord.ButtonStyle.red)
    async def confirmer(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.action_taken:
            await interaction.response.send_message("⚠️ Déjà effectué.", ephemeral=True)
            return
        self.action_taken = True
        self._disable_all()
        self.stop()
        embed = discord.Embed(title="🔒 Fermeture en cours…", description="Suppression dans **5 secondes**.", color=0x2ECC71)
        await interaction.response.edit_message(embed=embed, view=self)
        await send_ticket_log(interaction.guild, interaction.channel, self.closer)
        await asyncio.sleep(5)
        try:
            await interaction.channel.delete()
        except discord.NotFound:
            pass

    @discord.ui.button(label="❌ Annuler", style=discord.ButtonStyle.grey)
    async def annuler(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.action_taken:
            await interaction.response.send_message("⚠️ Déjà effectué.", ephemeral=True)
            return
        self.action_taken = True
        self._disable_all()
        self.stop()
        embed = discord.Embed(title="❌ Fermeture annulée", description="Le ticket reste ouvert.", color=0x95A5A6)
        await interaction.response.edit_message(embed=embed, view=self)


# ═══════════════════════════════════════════════════════════════
#  CRÉATION TICKET
# ═══════════════════════════════════════════════════════════════
async def creer_ticket(interaction: discord.Interaction, type_ticket: str):
    guild    = interaction.guild
    role     = guild.get_role(ROLE_ID)
    category = guild.get_channel(CATEGORY_ID)
    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        interaction.user:   discord.PermissionOverwrite(view_channel=True, send_messages=True),
        role:               discord.PermissionOverwrite(view_channel=True, send_messages=True),
    }
    channel = await guild.create_text_channel(f"ticket-{interaction.user.name}", category=category, overwrites=overwrites)
    if type_ticket == "recrutement":
        texte = (
            f"{role.mention} | {interaction.user.mention}\n\n"
            f"📋 **FORMULAIRE DE RECRUTEMENT – LA MYSTIC**\n\n"
            f"**1️⃣ Présentation personnelle**\n➤ Pseudo EXACT en jeu :\n➤ Âge (minimum 14 ans) :\n"
            f"➤ Style de jeu : (PvP / Farm / Build / Polyvalent)\n➤ Expérience / Points forts :\n\n"
            f"**2️⃣ Objectifs**\n➤ Court terme :\n➤ Long terme :\n\n"
            f"**3️⃣ Motivation**\n➤ Pourquoi rejoindre la Mystic ?\n➤ Ce que tu recherches :\n➤ Ce que tu apportes :\n\n"
            f"**4️⃣ Historique**\n➤ Anciennes factions :\n➤ Raison de départ :\n\n"
            f"**5️⃣ Stuff actuel**\n➤ Plateforme : (PS / Xbox / PC / Mobile)\n➤ Armure, armes, enchantements :\n\n"
            f"**6️⃣ Disponibilités**\n➤ Jours par semaine :\n➤ Plages horaires :\n\n"
            f"**7️⃣ Auto-critique**\n➤ Point faible en faction ?\n\n"
            f"**8️⃣ Mentalité**\n➤ Membre idéal ?\n➤ Vision du travail d'équipe ?\n\n"
            f"**9️⃣ Infos complémentaires**\n➤ Screenshots OBLIGATOIRES\n➤ Autres infos :\n\n"
            f"**✅ Confirmation**\n☐ J'ai 14 ans ou plus\n☐ Je respecterai les règles\n☐ Toute fausse info = refus"
        )
    else:
        texte = f"{role.mention} | {interaction.user.mention}\n\n📩 **Autre demande**\n\nExplique ta demande, un membre te répondra.\nPour fermer : `!fermer`"
    await channel.send(texte)
    await interaction.response.send_message(f"✅ Ticket créé : {channel.mention}", ephemeral=True)


# ═══════════════════════════════════════════════════════════════
#  COMMANDES TICKETS
# ═══════════════════════════════════════════════════════════════
@bot.command()
async def ticket(ctx):
    role_autorise = ctx.guild.get_role(ROLE_AUTORISE)
    if role_autorise not in ctx.author.roles:
        await ctx.send("❌ Permission refusée.", delete_after=5)
        return
    embed = discord.Embed(title="🎫 Ouvrir un ticket", description="Choisis le type de demande :", color=0x9B59B6)
    await ctx.send(embed=embed, view=TicketView())


@bot.command()
async def fermer(ctx):
    if "ticket-" not in ctx.channel.name:
        await ctx.send("❌ Uniquement dans un ticket.", delete_after=5)
        return
    view  = FermerView(closer=ctx.author)
    embed = discord.Embed(title="🔒 Fermer le ticket", description="Es-tu sûr ?\n\n⏳ Expiration dans **30s**…", color=0xFF0000)
    embed.set_footer(text="Aucune action = ticket conservé")
    msg = await ctx.send(embed=embed, view=view)
    asyncio.create_task(view.update_countdown(msg))
    await view.wait()


# ═══════════════════════════════════════════════════════════════
#  COMMANDES ROSTER
# ═══════════════════════════════════════════════════════════════
@bot.command()
async def roster(ctx):
    if not is_staff(ctx.author):
        await ctx.send("❌ Permission refusée.", delete_after=5)
        return
    try:
        channel = ctx.guild.get_channel(ROSTER_CHANNEL_ID) or await ctx.guild.fetch_channel(ROSTER_CHANNEL_ID)
    except Exception:
        await ctx.send("❌ Salon roster introuvable.", delete_after=5)
        return
    embed    = build_roster_embed(ctx.guild)
    existing = None
    async for msg in channel.history(limit=20):
        if msg.author == bot.user and msg.embeds:
            existing = msg
            break
    if existing:
        await existing.edit(embed=embed)
        await ctx.send("✅ Roster mis à jour !", delete_after=5)
    else:
        await channel.send(embed=embed)
        await ctx.send(f"✅ Roster posté dans {channel.mention} !", delete_after=5)


# ═══════════════════════════════════════════════════════════════
#  COMMANDES MODÉRATION
# ═══════════════════════════════════════════════════════════════
@bot.command()
async def ban(ctx, member: discord.Member = None, *, reason: str = "Aucune raison fournie"):
    if not is_staff(ctx.author):
        await ctx.send("❌ Permission refusée.", delete_after=5); return
    if member is None:
        await ctx.send("❌ Utilisation : `!ban @membre raison`", delete_after=5); return
    if not ctx.guild.me.guild_permissions.ban_members:
        await ctx.send("❌ Je n'ai pas la permission de bannir.", delete_after=5); return
    try:
        await member.ban(reason=reason, delete_message_days=1)
        await ctx.send(f"🔨 **{member}** a été banni. Raison : {reason}")
        embed = discord.Embed(title="🔨 Ban", color=0xE74C3C, timestamp=now_utc())
        embed.add_field(name="👤 Membre",     value=f"{member} ({member.id})", inline=True)
        embed.add_field(name="🛡️ Modérateur", value=ctx.author.mention,       inline=True)
        embed.add_field(name="📝 Raison",     value=reason,                   inline=False)
        embed.add_field(name="🕐 Date",       value=now_str(),                 inline=False)
        await send_log(ctx.guild, embed)
    except discord.Forbidden:
        await ctx.send("❌ Je ne peux pas bannir ce membre.", delete_after=5)


@bot.command()
async def kick(ctx, member: discord.Member = None, *, reason: str = "Aucune raison fournie"):
    if not is_staff(ctx.author):
        await ctx.send("❌ Permission refusée.", delete_after=5); return
    if member is None:
        await ctx.send("❌ Utilisation : `!kick @membre raison`", delete_after=5); return
    if not ctx.guild.me.guild_permissions.kick_members:
        await ctx.send("❌ Je n'ai pas la permission de kick.", delete_after=5); return
    try:
        await member.kick(reason=reason)
        await ctx.send(f"👢 **{member}** a été expulsé. Raison : {reason}")
        embed = discord.Embed(title="👢 Kick", color=0xE67E22, timestamp=now_utc())
        embed.add_field(name="👤 Membre",     value=f"{member} ({member.id})", inline=True)
        embed.add_field(name="🛡️ Modérateur", value=ctx.author.mention,       inline=True)
        embed.add_field(name="📝 Raison",     value=reason,                   inline=False)
        embed.add_field(name="🕐 Date",       value=now_str(),                 inline=False)
        await send_log(ctx.guild, embed)
    except discord.Forbidden:
        await ctx.send("❌ Je ne peux pas kick ce membre.", delete_after=5)


@bot.command()
async def mute(ctx, member: discord.Member = None, *, reason: str = "Aucune raison fournie"):
    if not is_staff(ctx.author):
        await ctx.send("❌ Permission refusée.", delete_after=5); return
    if member is None:
        await ctx.send("❌ Utilisation : `!mute @membre raison`", delete_after=5); return
    mute_role = discord.utils.get(ctx.guild.roles, name="Muted")
    if not mute_role:
        mute_role = await ctx.guild.create_role(name="Muted", reason="Création auto")
        for ch in ctx.guild.channels:
            await ch.set_permissions(mute_role, send_messages=False, speak=False)
    await member.add_roles(mute_role, reason=reason)
    await ctx.send(f"🔇 **{member}** a été mute. Raison : {reason}")
    embed = discord.Embed(title="🔇 Mute", color=0xE67E22, timestamp=now_utc())
    embed.add_field(name="👤 Membre",     value=f"{member} ({member.id})", inline=True)
    embed.add_field(name="🛡️ Modérateur", value=ctx.author.mention,       inline=True)
    embed.add_field(name="📝 Raison",     value=reason,                   inline=False)
    embed.add_field(name="🕐 Date",       value=now_str(),                 inline=False)
    await send_log(ctx.guild, embed)


@bot.command()
async def unmute(ctx, member: discord.Member = None):
    if not is_staff(ctx.author):
        await ctx.send("❌ Permission refusée.", delete_after=5); return
    if member is None:
        await ctx.send("❌ Utilisation : `!unmute @membre`", delete_after=5); return
    mute_role = discord.utils.get(ctx.guild.roles, name="Muted")
    if not mute_role or mute_role not in member.roles:
        await ctx.send("✅ Ce membre n'est pas mute.", delete_after=5); return
    await member.remove_roles(mute_role)
    await ctx.send(f"🔊 **{member}** a été unmute.")
    embed = discord.Embed(title="🔊 Unmute", color=0x2ECC71, timestamp=now_utc())
    embed.add_field(name="👤 Membre",     value=f"{member} ({member.id})", inline=True)
    embed.add_field(name="🛡️ Modérateur", value=ctx.author.mention,       inline=True)
    embed.add_field(name="🕐 Date",       value=now_str(),                 inline=False)
    await send_log(ctx.guild, embed)


@bot.command()
async def effacer(ctx, nombre: int = None):
    if not is_staff(ctx.author):
        await ctx.send("❌ Permission refusée.", delete_after=5); return
    if nombre is None:
        await ctx.send("❌ Utilisation : `!effacer 10`", delete_after=5); return
    if nombre < 1 or nombre > 100:
        await ctx.send("❌ Entre un nombre entre 1 et 100.", delete_after=5); return
    deleted = await ctx.channel.purge(limit=nombre + 1)
    await ctx.send(f"🗑️ **{len(deleted) - 1}** messages supprimés.", delete_after=5)
    embed = discord.Embed(title="🗑️ Purge", color=0x95A5A6, timestamp=now_utc())
    embed.add_field(name="🛡️ Modérateur", value=ctx.author.mention,    inline=True)
    embed.add_field(name="📍 Salon",      value=ctx.channel.mention,   inline=True)
    embed.add_field(name="🗑️ Supprimés", value=str(len(deleted) - 1), inline=True)
    embed.add_field(name="🕐 Date",       value=now_str(),              inline=False)
    await send_log(ctx.guild, embed)


# ═══════════════════════════════════════════════════════════════
#  COMMANDE INFO
# ═══════════════════════════════════════════════════════════════
@bot.command()
async def info(ctx, member: discord.Member = None):
    member   = member or ctx.author
    roles    = [r.mention for r in reversed(member.roles) if r.name != "@everyone"]
    top_role = member.top_role.mention if member.top_role.name != "@everyone" else "Aucun"
    perms = []
    if member.guild_permissions.administrator:   perms.append("👑 Administrateur")
    if member.guild_permissions.manage_guild:    perms.append("⚙️ Gérer le serveur")
    if member.guild_permissions.ban_members:     perms.append("🔨 Bannir")
    if member.guild_permissions.kick_members:    perms.append("👢 Expulser")
    if member.guild_permissions.manage_messages: perms.append("🗑️ Gérer messages")
    if member.guild_permissions.manage_roles:    perms.append("🎭 Gérer rôles")
    status_map = {
        discord.Status.online:  "🟢 En ligne",
        discord.Status.idle:    "🟡 Absent",
        discord.Status.dnd:     "🔴 Ne pas déranger",
        discord.Status.offline: "⚫ Hors ligne",
    }
    status   = status_map.get(member.status, "⚫ Inconnu")
    activity = "Aucune"
    if member.activity:
        if isinstance(member.activity, discord.Game):             activity = f"🎮 {member.activity.name}"
        elif isinstance(member.activity, discord.Streaming):      activity = f"📺 {member.activity.name}"
        elif isinstance(member.activity, discord.CustomActivity): activity = f"💬 {member.activity.name}"
        else:                                                      activity = member.activity.name
    embed = discord.Embed(
        title=f"👤 {member.display_name}",
        color=member.color if member.color != discord.Color.default() else 0x3498DB,
        timestamp=now_utc()
    )
    embed.set_thumbnail(url=member.display_avatar.url)
    if member.banner:
        embed.set_image(url=member.banner.url)
    embed.add_field(name="📛 Pseudo",         value=member.display_name, inline=True)
    embed.add_field(name="🏷️ Tag",            value=str(member),         inline=True)
    embed.add_field(name="🤖 Bot",             value="✅" if member.bot else "❌", inline=True)
    embed.add_field(name="🆔 ID",              value=str(member.id),     inline=True)
    embed.add_field(name="📅 Compte créé",     value=discord.utils.format_dt(member.created_at, style="D"), inline=True)
    embed.add_field(name="📥 Arrivée serveur", value=discord.utils.format_dt(member.joined_at, style="D") if member.joined_at else "?", inline=True)
    embed.add_field(name="📶 Statut",          value=status,             inline=True)
    embed.add_field(name="🎯 Activité",        value=activity,           inline=True)
    embed.add_field(name="🎖️ Rôle principal",  value=top_role,           inline=True)
    embed.add_field(name=f"🎭 Rôles ({len(roles)})", value=", ".join(roles[:20]) or "Aucun", inline=False)
    embed.add_field(name="🔑 Permissions",     value=", ".join(perms) or "Aucune", inline=False)
    embed.set_footer(text=f"Demandé par {ctx.author}")
    await ctx.send(embed=embed)


# ═══════════════════════════════════════════════════════════════
#  ON_MESSAGE : ANTI-LIENS + ANTI-SPAM
# ═══════════════════════════════════════════════════════════════
@bot.event
async def on_message(message: discord.Message):
    if message.author.bot or not message.guild:
        await bot.process_commands(message)
        return
    member = message.author

    # ── Anti-liens ──
    url_pattern = re.compile(r"(https?://|www\.)\S+", re.IGNORECASE)
    if url_pattern.search(message.content):
        if not member.guild_permissions.administrator:
            domain_match = re.search(r"(?:https?://|www\.)([^/\s]+)", message.content, re.IGNORECASE)
            domain = domain_match.group(1).lower() if domain_match else ""
            if not any(domain == d or domain.endswith("." + d) for d in ALLOWED_DOMAINS):
                print(f"[ANTI-LIENS] {member} : {message.content[:80]}")
                try:
                    await message.delete()
                    await message.channel.send(
                        f"❌ {member.mention} Tu n'as pas la permission d'envoyer des liens ici.",
                        delete_after=6
                    )
                    embed = discord.Embed(title="🔗 Lien bloqué", color=0xE74C3C, timestamp=now_utc())
                    embed.add_field(name="👤 Auteur",  value=f"{member} ({member.id})", inline=True)
                    embed.add_field(name="📍 Salon",   value=message.channel.mention,   inline=True)
                    embed.add_field(name="💬 Contenu", value=message.content[:500],     inline=False)
                    await send_log(message.guild, embed)
                except discord.Forbidden:
                    print("[ANTI-LIENS] Permission manquante — active 'Gérer les messages' pour le bot")
                except Exception as e:
                    print(f"[ANTI-LIENS] Erreur : {e}")
                return

    # ── Anti-spam ──
    if not is_staff(member):
        now = time.monotonic()
        spam_tracker[member.id].append(now)
        spam_tracker[member.id] = [t for t in spam_tracker[member.id] if now - t <= SPAM_WINDOW]
        count = len(spam_tracker[member.id])
        print(f"[ANTI-SPAM] {member} : {count}/{SPAM_LIMIT} msgs en {SPAM_WINDOW}s")

        if count > SPAM_LIMIT:
            if member.id in spam_warned:
                print(f"[ANTI-SPAM] Kick de {member}")
                spam_warned.discard(member.id)
                spam_tracker.pop(member.id, None)
                try:
                    await member.kick(reason="Anti-spam automatique")
                    await message.channel.send(f"🚫 {member.mention} expulsé pour spam répété.", delete_after=10)
                    embed = discord.Embed(title="🚫 Kick Anti-Spam", color=0xE74C3C, timestamp=now_utc())
                    embed.add_field(name="👤 Membre", value=f"{member} ({member.id})", inline=True)
                    embed.add_field(name="📍 Salon",  value=message.channel.mention,   inline=True)
                    await send_log(message.guild, embed)
                except discord.Forbidden:
                    print("[ANTI-SPAM] Permission manquante pour kick")
            else:
                print(f"[ANTI-SPAM] Avertissement de {member}")
                spam_warned.add(member.id)
                spam_tracker[member.id] = []
                await message.channel.send(
                    f"⚠️ {member.mention} **Stop le spam !** Prochaine fois = **expulsion automatique**.",
                    delete_after=10
                )

    await bot.process_commands(message)


# ═══════════════════════════════════════════════════════════════
#  LOGS AUTO
# ═══════════════════════════════════════════════════════════════
@bot.event
async def on_message_delete(message: discord.Message):
    if message.author.bot or not message.guild:
        return
    embed = discord.Embed(title="🗑️ Message supprimé", color=0x95A5A6, timestamp=now_utc())
    embed.add_field(name="👤 Auteur",  value=f"{message.author} ({message.author.id})", inline=True)
    embed.add_field(name="📍 Salon",   value=message.channel.mention,                   inline=True)
    embed.add_field(name="💬 Contenu", value=message.content[:1000] or "<vide>",        inline=False)
    embed.add_field(name="🆔 ID",      value=str(message.id),                           inline=True)
    await send_log(message.guild, embed)


@bot.event
async def on_message_edit(before: discord.Message, after: discord.Message):
    if before.author.bot or not before.guild or before.content == after.content:
        return
    embed = discord.Embed(title="✏️ Message modifié", color=0x3498DB, timestamp=now_utc())
    embed.add_field(name="👤 Auteur", value=f"{before.author} ({before.author.id})", inline=True)
    embed.add_field(name="📍 Salon",  value=before.channel.mention,                  inline=True)
    embed.add_field(name="📝 Avant",  value=before.content[:500] or "<vide>",        inline=False)
    embed.add_field(name="📝 Après",  value=after.content[:500] or "<vide>",         inline=False)
    embed.add_field(name="🔗 Lien",   value=f"[Voir]({after.jump_url})",             inline=True)
    await send_log(before.guild, embed)


@bot.event
async def on_member_join(member: discord.Member):
    embed = discord.Embed(title="📥 Membre arrivé", color=0x2ECC71, timestamp=now_utc())
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.add_field(name="👤 Membre",      value=f"{member} ({member.id})", inline=True)
    embed.add_field(name="📅 Compte créé", value=discord.utils.format_dt(member.created_at, style="D"), inline=True)
    embed.add_field(name="👥 Total",       value=str(member.guild.member_count), inline=True)
    await send_log(member.guild, embed)


@bot.event
async def on_member_remove(member: discord.Member):
    embed = discord.Embed(title="📤 Membre parti", color=0xE74C3C, timestamp=now_utc())
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.add_field(name="👤 Membre", value=f"{member} ({member.id})", inline=True)
    embed.add_field(name="👥 Total",  value=str(member.guild.member_count), inline=True)
    await send_log(member.guild, embed)


@bot.event
async def on_member_update(before: discord.Member, after: discord.Member):
    roster_role_ids = {r[0] for r in ROSTER_ROLES}
    before_ids = {r.id for r in before.roles}
    after_ids  = {r.id for r in after.roles}
    if before_ids & roster_role_ids != after_ids & roster_role_ids:
        try:
            channel = after.guild.get_channel(ROSTER_CHANNEL_ID) or await after.guild.fetch_channel(ROSTER_CHANNEL_ID)
            embed   = build_roster_embed(after.guild)
            async for msg in channel.history(limit=20):
                if msg.author == bot.user and msg.embeds:
                    await msg.edit(embed=embed)
                    return
            await channel.send(embed=embed)
        except Exception:
            pass
    added   = set(after.roles) - set(before.roles)
    removed = set(before.roles) - set(after.roles)
    if added or removed:
        embed = discord.Embed(title="🎭 Rôles modifiés", color=0x9B59B6, timestamp=now_utc())
        embed.add_field(name="👤 Membre", value=f"{after} ({after.id})", inline=True)
        if added:
            embed.add_field(name="✅ Ajoutés",  value=", ".join(r.mention for r in added),   inline=False)
        if removed:
            embed.add_field(name="❌ Retirés",  value=", ".join(r.mention for r in removed), inline=False)
        await send_log(after.guild, embed)


# ═══════════════════════════════════════════════════════════════
#  SYSTÈME DE TRACKING MINECRAFT BEDROCK
# ═══════════════════════════════════════════════════════════════

# ─────────────────────────────────────────────
#  Utilitaires temps
# ─────────────────────────────────────────────
def fmt_time(seconds: float) -> str:
    seconds = int(seconds)
    if seconds <= 0:
        return "0s"
    h, r = divmod(seconds, 3600)
    m, s = divmod(r, 60)
    parts = []
    if h: parts.append(f"{h}h")
    if m: parts.append(f"{m}m")
    if s or not parts: parts.append(f"{s}s")
    return " ".join(parts)


def player_key(pseudo: str, server: str) -> str:
    return f"{pseudo.lower()}@{server}"


# ─────────────────────────────────────────────
#  Stockage JSON
# ─────────────────────────────────────────────
def load_data() -> dict:
    if Path(TRACKING_FILE).exists():
        try:
            with open(TRACKING_FILE, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def save_data(data: dict):
    try:
        with open(TRACKING_FILE, "w") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        print(f"[TRACKING] Erreur sauvegarde JSON : {e}")


def get_player(data: dict, key: str) -> dict:
    if key not in data:
        now_iso = now_utc().isoformat()
        parts   = key.split("@", 1)
        data[key] = {
            "pseudo":         parts[0],
            "server":         parts[1] if len(parts) > 1 else "?",
            "online":         False,
            "last_seen":      None,
            "session_start":  None,
            "playtime_total": 0.0,
            "playtime_day":   0.0,
            "playtime_week":  0.0,
            "playtime_month": 0.0,
            "reset_day":      now_iso,
            "reset_week":     now_iso,
            "reset_month":    now_iso,
        }
    return data[key]


# ─────────────────────────────────────────────
#  Reset automatique des compteurs
# ─────────────────────────────────────────────
def apply_resets(p: dict):
    now = now_utc()
    last_day   = datetime.fromisoformat(p["reset_day"]).replace(tzinfo=timezone.utc)
    last_week  = datetime.fromisoformat(p["reset_week"]).replace(tzinfo=timezone.utc)
    last_month = datetime.fromisoformat(p["reset_month"]).replace(tzinfo=timezone.utc)
    if now.date() > last_day.date():
        p["playtime_day"]  = 0.0
        p["reset_day"]     = now.isoformat()
    if now.isocalendar()[1] != last_week.isocalendar()[1] or now.year != last_week.year:
        p["playtime_week"] = 0.0
        p["reset_week"]    = now.isoformat()
    if now.month != last_month.month or now.year != last_month.year:
        p["playtime_month"] = 0.0
        p["reset_month"]    = now.isoformat()


# ─────────────────────────────────────────────
#  Ping Bedrock UDP
# ─────────────────────────────────────────────
def ping_bedrock(host: str, port: int, timeout: float = 5.0) -> dict | None:
    MAGIC       = b"\x00\xff\xff\x00\xfe\xfe\xfe\xfe\xfd\xfd\xfd\xfd\x12\x34\x56\x78"
    CLIENT_GUID = b"\x00" * 8
    timestamp   = struct.pack(">Q", int(time.time() * 1000) & 0xFFFFFFFFFFFFFFFF)
    packet      = b"\x01" + timestamp + MAGIC + CLIENT_GUID
    try:
        addr_info = socket.getaddrinfo(host, port, socket.AF_INET, socket.SOCK_DGRAM)
        ip        = addr_info[0][4][0]
        sock      = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(timeout)
        sock.sendto(packet, (ip, port))
        data, _   = sock.recvfrom(2048)
        sock.close()
        if len(data) < 35:
            return {"online": True, "online_players": 0, "max_players": 0, "motd": ""}
        str_len  = struct.unpack(">H", data[33:35])[0]
        motd_raw = data[35:35 + str_len].decode("utf-8", errors="ignore")
        parts    = motd_raw.split(";")
        op = int(parts[4]) if len(parts) > 4 and parts[4].isdigit() else 0
        mp = int(parts[5]) if len(parts) > 5 and parts[5].isdigit() else 0
        return {
            "online":         True,
            "online_players": op,
            "max_players":    mp,
            "motd":           parts[1] if len(parts) > 1 else "",
        }
    except socket.gaierror as e:
        print(f"[TRACKING] DNS {host} : {e}")
        return None
    except socket.timeout:
        print(f"[TRACKING] Timeout {host}:{port}")
        return None
    except Exception as e:
        print(f"[TRACKING] Erreur ping {host}:{port} : {e}")
        return None


# ─────────────────────────────────────────────
#  Construction embed joueur
# ─────────────────────────────────────────────
def build_player_embed(p: dict, response: dict | None = None) -> discord.Embed:
    apply_resets(p)
    now = now_utc()

    live_seconds = 0.0
    if p["online"] and p["session_start"]:
        session_start = datetime.fromisoformat(p["session_start"]).replace(tzinfo=timezone.utc)
        live_seconds  = (now - session_start).total_seconds()

    status_emoji = "🟢 **En ligne**" if p["online"] else "🔴 **Hors ligne**"

    last_seen = "Jamais"
    if p["last_seen"]:
        last_seen_dt = datetime.fromisoformat(p["last_seen"]).replace(tzinfo=timezone.utc)
        last_seen    = discord.utils.format_dt(last_seen_dt, style="F")

    # Infos serveur en temps réel
    server_info = ""
    if response:
        server_info = f" *(👥 {response.get('online_players', '?')}/{response.get('max_players', '?')})*"

    color = 0x2ECC71 if p["online"] else 0xE74C3C
    embed = discord.Embed(title=f"🎮 Tracking — {p['pseudo']}", color=color, timestamp=now)
    embed.add_field(name="👤 Joueur",             value=p["pseudo"],                                  inline=True)
    embed.add_field(name="🌐 Serveur",            value=f"`{p['server']}`{server_info}",              inline=True)
    embed.add_field(name="📶 Statut",             value=status_emoji,                                 inline=True)
    embed.add_field(name="🕒 Dernière connexion", value=last_seen,                                    inline=False)
    embed.add_field(name="⏱️ Aujourd'hui",        value=fmt_time(p["playtime_day"]   + live_seconds), inline=True)
    embed.add_field(name="📅 Cette semaine",      value=fmt_time(p["playtime_week"]  + live_seconds), inline=True)
    embed.add_field(name="🗓️ Ce mois",           value=fmt_time(p["playtime_month"] + live_seconds), inline=True)
    embed.add_field(name="🧮 Total",              value=fmt_time(p["playtime_total"] + live_seconds), inline=True)
    embed.set_footer(text="🔄 Mise à jour toutes les 10s • Tracking approximatif Bedrock")
    return embed


# ─────────────────────────────────────────────
#  Boucle de tracking
# ─────────────────────────────────────────────
async def tracking_loop(key: str):
    print(f"[TRACKING] Boucle démarrée pour {key}")
    while key in active_trackers:
        try:
            data = load_data()
            p    = get_player(data, key)
            info = active_trackers[key]
            now  = now_utc()

            host, port_str = p["server"].rsplit(":", 1)
            port           = int(port_str)

            loop     = asyncio.get_event_loop()
            response = await loop.run_in_executor(None, ping_bedrock, host, port)

            server_up  = response is not None
            was_online = p["online"]
            # CORRECTION : online seulement si serveur répond ET au moins 1 joueur
            now_online = server_up and response is not None and response.get("online_players", 0) > 0

            if not was_online and now_online:
                p["online"]       = True
                p["session_start"] = now.isoformat()
                p["last_seen"]    = now.isoformat()
                print(f"[TRACKING] {p['pseudo']} → 🟢 ONLINE")

            elif was_online and not now_online:
                if p["session_start"]:
                    s_start  = datetime.fromisoformat(p["session_start"]).replace(tzinfo=timezone.utc)
                    duration = (now - s_start).total_seconds()
                    apply_resets(p)
                    p["playtime_total"]  += duration
                    p["playtime_day"]    += duration
                    p["playtime_week"]   += duration
                    p["playtime_month"]  += duration
                    print(f"[TRACKING] {p['pseudo']} → 🔴 OFFLINE (+{fmt_time(duration)})")
                p["online"]        = False
                p["session_start"] = None
                p["last_seen"]     = now.isoformat()

            elif now_online:
                p["last_seen"] = now.isoformat()

            save_data(data)

            # Met à jour l'embed
            channel = bot.get_channel(info["channel_id"])
            if channel:
                try:
                    msg   = await channel.fetch_message(info["msg_id"])
                    embed = build_player_embed(p, response)
                    await msg.edit(embed=embed)
                except discord.NotFound:
                    print(f"[TRACKING] Message supprimé pour {key} — arrêt du tracking")
                    active_trackers.pop(key, None)
                    return
                except Exception as e:
                    print(f"[TRACKING] Erreur edit embed {key} : {e}")

        except Exception as e:
            print(f"[TRACKING] Erreur boucle {key} : {e}")

        await asyncio.sleep(10)

    print(f"[TRACKING] Boucle arrêtée pour {key}")


# ─────────────────────────────────────────────
#  Commande !tracking
# ─────────────────────────────────────────────
@bot.command(name="tracking")
async def tracking_cmd(ctx, pseudo: str = None, server: str = None):
    if not is_staff(ctx.author):
        await ctx.send("❌ Permission refusée.", delete_after=5)
        return
    if pseudo is None or server is None:
        await ctx.send(
            "❌ Utilisation : `!tracking [joueur] [ip:port]`\n"
            "Exemple : `!tracking Steve play.paladium.fr:19132`",
            delete_after=10
        )
        return
    if ":" not in server:
        server = server + ":19132"
    try:
        host, port_str = server.rsplit(":", 1)
        port = int(port_str)
        if not (1 <= port <= 65535):
            raise ValueError
    except ValueError:
        await ctx.send("❌ Port invalide. Exemple : `play.paladium.fr:19132`", delete_after=8)
        return

    key = player_key(pseudo, server)

    # Arrête un tracker existant sur le même joueur
    if key in active_trackers:
        active_trackers.pop(key)
        await asyncio.sleep(0.5)

    # Test de connexion
    msg_wait = await ctx.send(f"🔍 Test de connexion à `{server}`…")
    loop     = asyncio.get_event_loop()
    response = await loop.run_in_executor(None, ping_bedrock, host, port)
    await msg_wait.delete()

    if response is None:
        await ctx.send(
            f"⚠️ **{server}** ne répond pas au ping Bedrock UDP.\n"
            f"Le tracking démarrera quand même et vérifiera toutes les 10s.",
            delete_after=12
        )
    else:
        op = response.get("online_players", "?")
        mp = response.get("max_players", "?")
        await ctx.send(
            f"✅ Serveur en ligne ! **{op}/{mp}** joueurs connectés.",
            delete_after=8
        )

    # Charge / crée le joueur
    data = load_data()
    p    = get_player(data, key)
    save_data(data)

    # Embed initial
    embed = build_player_embed(p, response)
    msg   = await ctx.send(embed=embed)

    # Enregistre le tracker
    active_trackers[key] = {
        "msg_id":    msg.id,
        "channel_id": ctx.channel.id,
    }

    # Lance la boucle
    asyncio.create_task(tracking_loop(key))
    print(f"[TRACKING] Démarré : {key} | msg={msg.id} | channel={ctx.channel.id}")


# ─────────────────────────────────────────────
#  Commande !stoptracking
# ─────────────────────────────────────────────
@bot.command(name="stoptracking")
async def stoptracking_cmd(ctx, pseudo: str = None, server: str = None):
    if not is_staff(ctx.author):
        await ctx.send("❌ Permission refusée.", delete_after=5)
        return
    if pseudo is None or server is None:
        await ctx.send("❌ Utilisation : `!stoptracking [joueur] [ip:port]`", delete_after=8)
        return
    if ":" not in server:
        server = server + ":19132"
    key = player_key(pseudo, server)
    if key in active_trackers:
        active_trackers.pop(key)
        await ctx.send(f"✅ Tracking arrêté pour **{pseudo}**.")
        print(f"[TRACKING] Arrêté manuellement : {key}")
    else:
        await ctx.send(f"❌ Aucun tracking actif pour **{pseudo}**.", delete_after=8)


# ─────────────────────────────────────────────
#  Commande !tracklist
# ─────────────────────────────────────────────
@bot.command(name="tracklist")
async def tracklist_cmd(ctx):
    if not is_staff(ctx.author):
        await ctx.send("❌ Permission refusée.", delete_after=5)
        return
    if not active_trackers:
        await ctx.send("📭 Aucun joueur en cours de tracking.", delete_after=8)
        return
    data  = load_data()
    lines = []
    for key, info in active_trackers.items():
        p      = data.get(key, {})
        pseudo = p.get("pseudo", key.split("@")[0])
        server = p.get("server", "?")
        status = "🟢" if p.get("online") else "🔴"
        ch     = f"<#{info['channel_id']}>"
        lines.append(f"{status} **{pseudo}** sur `{server}` → {ch}")
    embed = discord.Embed(
        title=f"📡 Joueurs trackés ({len(active_trackers)})",
        description="\n".join(lines),
        color=0x3498DB,
        timestamp=now_utc()
    )
    await ctx.send(embed=embed)


# ─────────────────────────────────────────────
#  Commande !classement
# ─────────────────────────────────────────────
@bot.command(name="classement", aliases=["leaderboard", "top"])
async def classement_cmd(ctx):
    if not is_staff(ctx.author):
        await ctx.send("❌ Permission refusée.", delete_after=5)
        return
    data = load_data()
    if not data:
        await ctx.send("❌ Aucun joueur suivi pour le moment.", delete_after=8)
        return

    now = now_utc()

    def live_extra(p: dict) -> float:
        if p.get("online") and p.get("session_start"):
            start = datetime.fromisoformat(p["session_start"]).replace(tzinfo=timezone.utc)
            return (now - start).total_seconds()
        return 0.0

    medals = ["🥇", "🥈", "🥉"]

    def build_top(field: str) -> str:
        players = []
        for p in data.values():
            apply_resets(p)
            val = p.get(field, 0.0) + live_extra(p)
            players.append((p.get("pseudo", "?"), val))
        players.sort(key=lambda x: x[1], reverse=True)
        players = [x for x in players if x[1] > 0][:10]
        if not players:
            return "_Aucun joueur_"
        lines = []
        for i, (pseudo, val) in enumerate(players):
            rank = medals[i] if i < 3 else f"`#{i+1}`"
            lines.append(f"{rank} **{pseudo}** — {fmt_time(val)}")
        return "\n".join(lines)

    embed = discord.Embed(title="🏆 Classement — La Mystic", color=0xF1C40F, timestamp=now)
    embed.add_field(name="🧮 Total",          value=build_top("playtime_total"), inline=False)
    embed.add_field(name="🗓️ Ce mois",        value=build_top("playtime_month"), inline=False)
    embed.add_field(name="📅 Cette semaine",  value=build_top("playtime_week"),  inline=False)
    embed.add_field(name="⏱️ Aujourd'hui",    value=build_top("playtime_day"),   inline=False)
    embed.set_footer(text="Temps live inclus • Tracking approximatif Bedrock")
    await ctx.send(embed=embed)


# ═══════════════════════════════════════════════════════════════
#  COMMANDE AIDE
# ═══════════════════════════════════════════════════════════════
bot.remove_command("help")

@bot.command(name="help", aliases=["aide", "commandes"])
async def help_cmd(ctx):
    staff = is_staff(ctx.author)
    embed = discord.Embed(
        title="📖 Aide — Commandes du bot",
        description="Voici toutes les commandes disponibles.\n*(🔒 = réservé au staff)*",
        color=0x9B59B6
    )
    embed.add_field(
        name="━━━━━━━━━━━━━━━━━━\n👤 Commandes générales",
        value="`!info @membre` — Infos complètes d'un membre\n`!help` — Affiche ce message",
        inline=False
    )
    embed.add_field(
        name="━━━━━━━━━━━━━━━━━━\n🎫 Tickets",
        value=(
            "`!ticket` 🔒 — Panneau d'ouverture de tickets\n"
            "`!fermer` — Ferme le ticket (génère un transcript)"
        ),
        inline=False
    )
    embed.add_field(
        name="━━━━━━━━━━━━━━━━━━\n📋 Roster",
        value="`!roster` 🔒 — Met à jour le roster de la faction",
        inline=False
    )
    if staff:
        embed.add_field(
            name="━━━━━━━━━━━━━━━━━━\n🔨 Modération 🔒",
            value=(
                "`!ban @membre [raison]` — Bannit un membre\n"
                "`!kick @membre [raison]` — Expulse un membre\n"
                "`!mute @membre [raison]` — Réduit un membre au silence\n"
                "`!unmute @membre` — Rend la parole à un membre\n"
                "`!effacer <nombre>` — Supprime des messages (max 100)"
            ),
            inline=False
        )
        embed.add_field(
            name="━━━━━━━━━━━━━━━━━━\n🎮 Tracking Minecraft Bedrock 🔒",
            value=(
                "`!tracking [joueur] [ip:port]` — Suit un joueur, embed mis à jour toutes les 10s\n"
                "`!stoptracking [joueur] [ip:port]` — Arrête le tracking\n"
                "`!tracklist` — Liste tous les joueurs en cours de suivi\n"
                "`!classement` — Top 10 par total / mois / semaine / jour\n\n"
                "⚠️ *Tracking approximatif : Bedrock ne donne pas la liste exacte des joueurs.*"
            ),
            inline=False
        )
    embed.add_field(
        name="━━━━━━━━━━━━━━━━━━\n🛡️ Protections automatiques",
        value=(
            "🔗 **Anti-liens** — Liens supprimés automatiquement (sauf admins)\n"
            "⚡ **Anti-spam** — +4 msgs en 6s = avertissement, récidive = expulsion"
        ),
        inline=False
    )
    embed.set_footer(text="🔒 = réservé aux Officiers et grades supérieurs")
    await ctx.send(embed=embed)


# ═══════════════════════════════════════════════════════════════
#  DÉMARRAGE
# ═══════════════════════════════════════════════════════════════
@bot.event
async def on_ready():
    print(f"✅ Mystic Bot connecté : {bot.user}")
    print(f"   LOG_CHANNEL_ID    = {LOG_CHANNEL_ID}")
    print(f"   ROSTER_CHANNEL_ID = {ROSTER_CHANNEL_ID}")
    print(f"   Anti-spam         : {SPAM_LIMIT} msgs / {SPAM_WINDOW}s")

    # ── Rechargement des trackers depuis le JSON au démarrage ──
    data = load_data()
    if data:
        print(f"[TRACKING] {len(data)} joueur(s) trouvé(s) dans le JSON — rechargement impossible sans msg_id")
        print(f"[TRACKING] Relancez !tracking pour chaque joueur à suivre.")
    print(f"[TRACKING] Prêt.")


TOKEN = os.environ.get("DISCORD_TOKEN")
bot.run(TOKEN)
