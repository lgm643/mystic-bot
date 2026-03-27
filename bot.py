import discord
from discord.ext import commands
import asyncio
import io
import os
import re
import time
from datetime import datetime, timezone
from collections import defaultdict

intents = discord.Intents.all()
bot = commands.Bot(command_prefix="!", intents=intents)

# ─────────────────────────────────────────────
#  CONSTANTES GÉNÉRALES
# ─────────────────────────────────────────────
ROLE_ID           = 913064374590140417
CATEGORY_ID       = 1419109736091095090
ROLE_AUTORISE     = 703339900929441803
LOG_CHANNEL_ID    = 713166766229946418
ROSTER_CHANNEL_ID   = 840695680288423976
WELCOME_CHANNEL_ID  = 744856318971740182

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
    # ── Message de bienvenue ──
    welcome_channel = member.guild.get_channel(WELCOME_CHANNEL_ID)
    if welcome_channel:
        try:
            await welcome_channel.send(
                f"Hey {member.mention} 👋\n"
                f"Bienvenue sur le Discord de **La Mystic** 👑\n"
                f"N'hésite pas à ouvrir un ticket si tu veux rejoindre la faction ou si t'as une question. On est là 🙌"
            )
        except Exception as e:
            print(f"[WELCOME] Erreur envoi bienvenue : {e}")

    # ── Log staff ──
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
        value="`!ticket` 🔒 — Panneau d'ouverture de tickets\n`!fermer` — Ferme le ticket",
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
                "`!mute @membre [raison]` — Réduit au silence\n"
                "`!unmute @membre` — Rend la parole\n"
                "`!effacer <n>` — Supprime n messages (max 100)"
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




# ═══════════════════════════════════════════════════════════════
#  SYSTÈME XP / NIVEAUX
# ═══════════════════════════════════════════════════════════════
import json
import random
import math
from pathlib import Path

DATA_FILE = "user_data.json"
xp_cooldowns: dict[int, float] = {}  # user_id → last_xp_time

FACTION_ROLE_IDS = {
    739879603497336928,  # Recrue
    703339648591855656,  # Membre
    722074234611826809,  # Membre +
    703339574515990549,  # Membre de confiance
    703344242017173524,  # Officier
    706808147796426783,  # Leader
}

GIVEAWAY_ROLE_IDS = {703344242017173524, 706808147796426783}  # Officier, Leader

# Salons où pendu/devine/mot sont autorisés en plus des salons normaux
PENDU_ALLOWED_EVERYWHERE = True
active_pendu: dict[int, dict] = {}  # channel_id → game state


# ─────────────────────────────────────────────
#  Utilitaires données
# ─────────────────────────────────────────────
def load_user_data() -> dict:
    if Path(DATA_FILE).exists():
        try:
            with open(DATA_FILE, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def save_user_data(data: dict):
    with open(DATA_FILE, "w") as f:
        json.dump(data, f, indent=2)


def get_user(data: dict, user_id: int) -> dict:
    uid = str(user_id)
    if uid not in data:
        data[uid] = {
            "xp": 0,
            "level": 0,
            "message_count": 0,
            "voice_time": 0.0,
            "voice_join": None,
        }
    return data[uid]


def xp_for_level(level: int) -> int:
    """XP total requis pour atteindre ce niveau."""
    return 100 * (level + 1) + 50 * level * level


def progress_bar(current: int, total: int, length: int = 10) -> str:
    filled = int(length * current / total) if total > 0 else 0
    return "█" * filled + "░" * (length - filled)


# ─────────────────────────────────────────────
#  Gain XP sur message
# ─────────────────────────────────────────────
@bot.listen("on_message")
async def xp_on_message(message: discord.Message):
    if message.author.bot or not message.guild:
        return
    uid  = message.author.id
    now  = time.monotonic()

    # Cooldown 10s
    if now - xp_cooldowns.get(uid, 0) < 10:
        return
    xp_cooldowns[uid] = now

    data = load_user_data()
    u    = get_user(data, uid)
    u["message_count"] += 1

    gained      = random.randint(5, 15)
    u["xp"]    += gained
    old_level   = u["level"]
    required    = xp_for_level(old_level + 1)

    # Level up
    if u["xp"] >= required:
        u["level"] += 1
        u["xp"]    -= required
        save_user_data(data)
        msg = await message.channel.send(
            f"🎉 {message.author.mention} passe niveau **{u['level']}** ! GG 🔥"
        )
        await asyncio.sleep(2)
        try:
            await msg.delete()
        except Exception:
            pass
        return

    save_user_data(data)


# ─────────────────────────────────────────────
#  Commande !level
# ─────────────────────────────────────────────
@bot.command(name="level", aliases=["lvl", "xp"])
async def level_cmd(ctx, member: discord.Member = None):
    member = member or ctx.author
    data   = load_user_data()
    u      = get_user(data, member.id)
    save_user_data(data)

    lvl      = u["level"]
    cur_xp   = u["xp"]
    required = xp_for_level(lvl + 1)
    bar      = progress_bar(cur_xp, required)

    embed = discord.Embed(
        title=f"📊 Niveau — {member.display_name}",
        color=member.color if member.color != discord.Color.default() else 0x9B59B6,
        timestamp=now_utc()
    )
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.add_field(name="🏆 Niveau",     value=str(lvl),                    inline=True)
    embed.add_field(name="✉️ Messages",   value=str(u["message_count"]),      inline=True)
    embed.add_field(name="🎤 Vocal",      value=fmt_voice(u["voice_time"]),   inline=True)
    embed.add_field(
        name=f"⭐ XP — {cur_xp}/{required}",
        value=f"`{bar}` {int(cur_xp/required*100)}%",
        inline=False
    )
    await ctx.send(embed=embed)


def fmt_voice(seconds: float) -> str:
    seconds = int(seconds)
    h, r = divmod(seconds, 3600)
    m, s = divmod(r, 60)
    if h:   return f"{h}h {m}m"
    if m:   return f"{m}m {s}s"
    return f"{s}s"


# ═══════════════════════════════════════════════════════════════
#  TRACKING VOCAL
# ═══════════════════════════════════════════════════════════════
@bot.event
async def on_voice_state_update(member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
    if member.bot:
        return
    data = load_user_data()
    u    = get_user(data, member.id)
    now  = time.time()

    # Rejoindre un canal
    if before.channel is None and after.channel is not None:
        u["voice_join"] = now

    # Quitter un canal
    elif before.channel is not None and after.channel is None:
        if u.get("voice_join"):
            duration          = now - u["voice_join"]
            u["voice_time"]  += duration
            u["voice_join"]   = None
            print(f"[VOCAL] {member} — +{int(duration)}s (total {int(u['voice_time'])}s)")

    save_user_data(data)


# ═══════════════════════════════════════════════════════════════
#  MINI-JEU : PILE OU FACE
# ═══════════════════════════════════════════════════════════════
@bot.command(name="pileouface", aliases=["pof", "coinflip"])
async def pof_cmd(ctx):
    result = random.choice(["🪙 **Pile**", "🔵 **Face**"])
    embed  = discord.Embed(
        title="🪙 Pile ou Face",
        description=f"Résultat : {result}",
        color=0xF1C40F
    )
    await ctx.send(embed=embed)


# ═══════════════════════════════════════════════════════════════
#  MINI-JEU : PENDU
# ═══════════════════════════════════════════════════════════════
PENDU_MOTS = [
    "faction", "alliance", "serveur", "minecraft", "bedrock", "armure",
    "epee", "bouclier", "ressource", "territoire", "combat", "recrue",
    "officier", "leader", "victoire", "défaite", "stratégie", "forteresse",
    "invasion", "guilde", "dragon", "creeper", "zombie", "squelette",
    "diamant", "emeraude", "netherite", "enchantement", "potion", "portail",
]

PENDU_ART = [
    "```\n  +---+\n      |\n      |\n      |\n      |\n=========```",
    "```\n  +---+\n  O   |\n      |\n      |\n      |\n=========```",
    "```\n  +---+\n  O   |\n  |   |\n      |\n      |\n=========```",
    "```\n  +---+\n  O   |\n /|   |\n      |\n      |\n=========```",
    "```\n  +---+\n  O   |\n /|\\  |\n      |\n      |\n=========```",
    "```\n  +---+\n  O   |\n /|\\  |\n /    |\n      |\n=========```",
    "```\n  +---+\n  O   |\n /|\\  |\n / \\  |\n      |\n=========```",
]


def build_pendu_embed(game: dict) -> discord.Embed:
    word    = game["word"]
    guessed = game["guessed"]
    errors  = game["errors"]
    display = " ".join(l if l in guessed else "\u005f" for l in word)
    wrong   = [l for l in guessed if l not in word]

    color = 0x2ECC71 if all(l in guessed for l in word) else (0xE74C3C if errors >= 6 else 0x9B59B6)
    embed = discord.Embed(title="🎯 Pendu — La Mystic", color=color)
    embed.add_field(name="Mot",        value=f"`{display}`",                    inline=False)
    embed.add_field(name="Pendu",      value=PENDU_ART[errors],                 inline=False)
    embed.add_field(name="❌ Erreurs", value=f"{errors}/6 — `{''.join(wrong) or 'aucune'}`", inline=True)
    embed.add_field(name="✅ Trouvées", value=f"`{''.join(sorted(l for l in guessed if l in word)) or 'aucune'}`", inline=True)
    if game.get("participants"):
        parts = ", ".join(f"<@{uid}>" for uid in game["participants"])
        embed.add_field(name="👥 Participants", value=parts, inline=False)
    embed.set_footer(text="!devine [lettre] ou !mot [mot]")
    return embed


class PenduView(discord.ui.View):
    def __init__(self, channel_id: int, creator_id: int):
        super().__init__(timeout=None)
        self.channel_id = channel_id
        self.creator_id = creator_id

    @discord.ui.button(label="🎲 Mot aléatoire", style=discord.ButtonStyle.green)
    async def random_word(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.creator_id:
            await interaction.response.send_message("❌ Seul le créateur peut lancer la partie.", ephemeral=True)
            return
        word = random.choice(PENDU_MOTS)
        await self._start(interaction, word)

    @discord.ui.button(label="✍️ Mot personnalisé", style=discord.ButtonStyle.blurple)
    async def custom_word(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.creator_id:
            await interaction.response.send_message("❌ Seul le créateur peut lancer la partie.", ephemeral=True)
            return
        await interaction.response.send_message("📩 Je t'ai envoyé un DM pour que tu entres le mot !", ephemeral=True)
        try:
            dm = await interaction.user.create_dm()
            await dm.send("✍️ Entre le mot pour le pendu (lettres minuscules sans accents) :")

            def check(m):
                return m.author.id == interaction.user.id and isinstance(m.channel, discord.DMChannel)

            dm_msg = await bot.wait_for("message", check=check, timeout=60)
            word   = dm_msg.content.strip().lower()
            if not word.isalpha():
                await dm.send("❌ Mot invalide (lettres uniquement).")
                return
            channel = bot.get_channel(self.channel_id)
            if channel:
                game = {
                    "word": word, "guessed": set(), "errors": 0,
                    "creator": interaction.user.id, "participants": [],
                    "msg_id": None, "letter_cd": {}
                }
                active_pendu[self.channel_id] = game
                embed = build_pendu_embed(game)
                msg   = await channel.send(embed=embed)
                game["msg_id"] = msg.id
                await dm.send(f"✅ Partie lancée avec le mot `{word}` !")
        except Exception as e:
            print(f"[PENDU] Erreur DM : {e}")

    async def _start(self, interaction: discord.Interaction, word: str):
        game = {
            "word": word, "guessed": set(), "errors": 0,
            "creator": interaction.user.id, "participants": [],
            "msg_id": None, "letter_cd": {}
        }
        active_pendu[self.channel_id] = game
        embed = build_pendu_embed(game)
        await interaction.response.edit_message(embed=embed, view=None)
        msg = await interaction.original_response()
        game["msg_id"] = msg.id


@bot.command(name="pendu")
async def pendu_cmd(ctx):
    if ctx.channel.id in active_pendu:
        await ctx.send("❌ Une partie est déjà en cours dans ce salon.", delete_after=5)
        return
    embed = discord.Embed(
        title="🎯 Pendu — Nouvelle partie",
        description="Choisis comment jouer :",
        color=0x9B59B6
    )
    view = PenduView(ctx.channel.id, ctx.author.id)
    await ctx.send(embed=embed, view=view)


@bot.command(name="devine")
async def devine_cmd(ctx, lettre: str = None):
    """Deviner une lettre au pendu."""
    game = active_pendu.get(ctx.channel.id)
    if not game:
        await ctx.send("❌ Aucune partie en cours. Lance `!pendu`.", delete_after=5)
        return
    if ctx.author.id == game["creator"]:
        await ctx.send("❌ Le créateur ne peut pas jouer.", delete_after=5)
        return
    if lettre is None or len(lettre) != 1 or not lettre.isalpha():
        await ctx.send("❌ Entre une seule lettre : `!devine a`", delete_after=5)
        return

    lettre = lettre.lower()
    uid    = ctx.author.id
    now    = time.monotonic()

    # Anti-spam lettres (3s par utilisateur)
    if now - game["letter_cd"].get(uid, 0) < 3:
        await ctx.send("⏳ Attends un peu avant de réessayer.", delete_after=3)
        return
    game["letter_cd"][uid] = now

    if lettre in game["guessed"]:
        await ctx.send(f"⚠️ La lettre `{lettre}` a déjà été jouée.", delete_after=4)
        return

    game["guessed"].add(lettre)
    if uid not in game["participants"]:
        game["participants"].append(uid)

    word = game["word"]
    if lettre not in word:
        game["errors"] += 1

    try:
        await ctx.message.delete()
    except Exception:
        pass

    await _update_pendu(ctx, game)


@bot.command(name="mot")
async def mot_cmd(ctx, *, mot: str = None):
    """Deviner le mot entier au pendu."""
    game = active_pendu.get(ctx.channel.id)
    if not game:
        await ctx.send("❌ Aucune partie en cours.", delete_after=5)
        return
    if ctx.author.id == game["creator"]:
        await ctx.send("❌ Le créateur ne peut pas jouer.", delete_after=5)
        return
    if mot is None:
        await ctx.send("❌ Utilisation : `!mot bonjour`", delete_after=5)
        return

    mot = mot.lower().strip()
    uid = ctx.author.id
    if uid not in game["participants"]:
        game["participants"].append(uid)

    try:
        await ctx.message.delete()
    except Exception:
        pass

    if mot == game["word"]:
        # Victoire par mot entier
        for l in game["word"]:
            game["guessed"].add(l)
    else:
        game["errors"] += 1

    await _update_pendu(ctx, game)


async def _update_pendu(ctx, game: dict):
    word     = game["word"]
    guessed  = game["guessed"]
    errors   = game["errors"]
    won      = all(l in guessed for l in word)
    lost     = errors >= 6

    # Met à jour l'embed
    channel = ctx.channel
    if game.get("msg_id"):
        try:
            msg   = await channel.fetch_message(game["msg_id"])
            embed = build_pendu_embed(game)
            await msg.edit(embed=embed)
        except Exception:
            pass

    if won:
        # +150 XP au(x) gagnant(s)
        data = load_user_data()
        for uid in game["participants"]:
            u = get_user(data, uid)
            u["xp"] += 150
        save_user_data(data)
        active_pendu.pop(ctx.channel.id, None)
        await ctx.send(
            f"🏆 Bravo ! Le mot était **{word}** !\n"
            f"Les participants gagnent **+150 XP** 🎉",
            delete_after=15
        )

    elif lost:
        active_pendu.pop(ctx.channel.id, None)
        await ctx.send(f"💀 Perdu ! Le mot était **{word}**.")
        # Mute les participants 20s
        mute_role = discord.utils.get(ctx.guild.roles, name="Muted")
        if not mute_role:
            mute_role = await ctx.guild.create_role(name="Muted")
            for ch in ctx.guild.channels:
                await ch.set_permissions(mute_role, send_messages=False, speak=False)
        for uid in game["participants"]:
            if uid == game["creator"]:
                continue
            member = ctx.guild.get_member(uid)
            if member:
                try:
                    await member.add_roles(mute_role, reason="Pendu perdu")
                except Exception:
                    pass
        await asyncio.sleep(20)
        for uid in game["participants"]:
            member = ctx.guild.get_member(uid)
            if member and mute_role in member.roles:
                try:
                    await member.remove_roles(mute_role)
                except Exception:
                    pass


# ═══════════════════════════════════════════════════════════════
#  GIVEAWAY
# ═══════════════════════════════════════════════════════════════
import re as _re

active_giveaways: dict[int, dict] = {}  # msg_id → giveaway


class GiveawayView(discord.ui.View):
    def __init__(self, msg_id: int):
        super().__init__(timeout=None)
        self.msg_id = msg_id

    @discord.ui.button(label="🎉 Participer", style=discord.ButtonStyle.green)
    async def participer(self, interaction: discord.Interaction, button: discord.ui.Button):
        gw = active_giveaways.get(self.msg_id)
        if not gw:
            await interaction.response.send_message("❌ Ce giveaway est terminé.", ephemeral=True)
            return
        uid = interaction.user.id
        if uid in gw["participants"]:
            gw["participants"].remove(uid)
            await interaction.response.send_message("❌ Tu t'es retiré du giveaway.", ephemeral=True)
        else:
            gw["participants"].append(uid)
            await interaction.response.send_message("✅ Tu participes au giveaway !", ephemeral=True)

        # Met à jour l'embed
        try:
            msg   = await interaction.channel.fetch_message(self.msg_id)
            embed = build_giveaway_embed(gw)
            await msg.edit(embed=embed)
        except Exception:
            pass


def build_giveaway_embed(gw: dict) -> discord.Embed:
    ends = discord.utils.format_dt(
        datetime.fromtimestamp(gw["ends_at"], tz=timezone.utc), style="R"
    )
    embed = discord.Embed(
        title=f"🎉 GIVEAWAY — {gw['reward']}",
        description=f"Clique sur **🎉 Participer** pour tenter ta chance !",
        color=0xF1C40F
    )
    embed.add_field(name="⏰ Fin",           value=ends,                    inline=True)
    embed.add_field(name="👥 Participants",  value=str(len(gw["participants"])), inline=True)
    embed.add_field(name="🏆 Récompense",   value=gw["reward"],            inline=False)
    embed.set_footer(text=f"Organisé par {gw['host']}")
    return embed


def parse_duration(s: str) -> int | None:
    """Convertit '10m', '1h', '2h30m', '30s' en secondes."""
    total = 0
    for val, unit in _re.findall(r"(\d+)([smhj])", s.lower()):
        v = int(val)
        if unit == "s": total += v
        elif unit == "m": total += v * 60
        elif unit == "h": total += v * 3600
        elif unit == "j": total += v * 86400
    return total if total > 0 else None


@bot.command(name="giveaway", aliases=["gw"])
async def giveaway_cmd(ctx, duree: str = None, *, reward: str = None):
    # Vérif rôle
    if not any(r.id in GIVEAWAY_ROLE_IDS for r in ctx.author.roles) and not ctx.author.guild_permissions.administrator:
        await ctx.send("❌ Réservé aux Officiers et Leaders.", delete_after=5)
        return
    if duree is None or reward is None:
        await ctx.send("❌ Utilisation : `!giveaway 1h Rang VIP`", delete_after=8)
        return

    seconds = parse_duration(duree)
    if not seconds:
        await ctx.send("❌ Durée invalide. Exemples : `10m`, `1h`, `2h30m`", delete_after=8)
        return

    ends_at = time.time() + seconds
    gw = {
        "reward":       reward,
        "ends_at":      ends_at,
        "participants": [],
        "host":         str(ctx.author),
        "channel_id":   ctx.channel.id,
    }

    embed = build_giveaway_embed(gw)
    msg   = await ctx.send(embed=embed, view=GiveawayView(0))  # msg_id updated below

    gw_id = msg.id
    active_giveaways[gw_id] = gw

    # Recrée la view avec le bon msg_id
    await msg.edit(view=GiveawayView(gw_id))

    # Lance le timer
    asyncio.create_task(_end_giveaway(gw_id, seconds, ctx.channel, reward))
    print(f"[GW] Giveaway lancé : {reward} — {seconds}s | msg={gw_id}")


async def _end_giveaway(gw_id: int, delay: int, channel: discord.TextChannel, reward: str):
    await asyncio.sleep(delay)
    gw = active_giveaways.pop(gw_id, None)
    if not gw:
        return

    participants = gw["participants"]
    try:
        msg = await channel.fetch_message(gw_id)
        if not participants:
            embed = discord.Embed(
                title=f"🎉 GIVEAWAY TERMINÉ — {reward}",
                description="😔 Aucun participant...",
                color=0x95A5A6
            )
            await msg.edit(embed=embed, view=None)
            return

        winner_id = random.choice(participants)
        winner    = channel.guild.get_member(winner_id)
        name      = winner.mention if winner else f"<@{winner_id}>"

        embed = discord.Embed(
            title=f"🎉 GIVEAWAY TERMINÉ — {reward}",
            description=f"🏆 Gagnant : {name}\n🎊 Félicitations !",
            color=0x2ECC71
        )
        embed.set_footer(text=f"Organisé par {gw['host']} • {len(participants)} participants")
        await msg.edit(embed=embed, view=None)
        await channel.send(f"🎊 Félicitations {name} ! Tu as gagné **{reward}** !")
    except Exception as e:
        print(f"[GW] Erreur fin giveaway : {e}")


# ═══════════════════════════════════════════════════════════════
#  CLASSEMENT GLOBAL + FACTION
# ═══════════════════════════════════════════════════════════════
@bot.command(name="classement", aliases=["top", "leaderboard"])
async def classement_cmd(ctx):
    data  = load_user_data()
    guild = ctx.guild

    # Enrichit avec voix live
    now = time.time()
    for uid_str, u in data.items():
        if u.get("voice_join"):
            u["_voice_live"] = u["voice_time"] + (now - u["voice_join"])
        else:
            u["_voice_live"] = u["voice_time"]

    medals = ["🥇", "🥈", "🥉"]

    def top10(key: str, fmt=str) -> str:
        items = sorted(
            [(uid, u) for uid, u in data.items() if u.get(key, 0) > 0],
            key=lambda x: x[1].get(key, 0),
            reverse=True
        )[:10]
        if not items:
            return "_Aucun joueur_"
        lines = []
        for i, (uid, u) in enumerate(items):
            m = guild.get_member(int(uid))
            name = m.display_name if m else f"Inconnu ({uid})"
            rank = medals[i] if i < 3 else f"`#{i+1}`"
            lines.append(f"{rank} **{name}** — {fmt(u.get(key, 0))}")
        return "\n".join(lines)

    def fmt_level(u_dict: dict) -> str:
        return f"Niv. {u_dict.get('level', 0)} ({u_dict.get('xp', 0)} XP)"

    # Top messages
    top_msg = top10("message_count", lambda v: f"{v} msg")

    # Top niveau
    items_lvl = sorted(
        [(uid, u) for uid, u in data.items()],
        key=lambda x: (x[1].get("level", 0), x[1].get("xp", 0)),
        reverse=True
    )[:10]
    top_lvl_lines = []
    for i, (uid, u) in enumerate(items_lvl):
        m    = guild.get_member(int(uid))
        name = m.display_name if m else f"Inconnu"
        rank = medals[i] if i < 3 else f"`#{i+1}`"
        top_lvl_lines.append(f"{rank} **{name}** — Niv. {u.get('level', 0)} ({u.get('xp', 0)} XP)")
    top_lvl = "\n".join(top_lvl_lines) or "_Aucun joueur_"

    # Top vocal
    items_vc = sorted(
        [(uid, u) for uid, u in data.items() if u.get("_voice_live", 0) > 0],
        key=lambda x: x[1].get("_voice_live", 0),
        reverse=True
    )[:10]
    top_vc_lines = []
    for i, (uid, u) in enumerate(items_vc):
        m    = guild.get_member(int(uid))
        name = m.display_name if m else f"Inconnu"
        rank = medals[i] if i < 3 else f"`#{i+1}`"
        top_vc_lines.append(f"{rank} **{name}** — {fmt_voice(u['_voice_live'])}")
    top_vc = "\n".join(top_vc_lines) or "_Aucun joueur_"

    # Classement faction (membres avec rôle faction actif)
    faction_members = []
    for uid_str, u in data.items():
        m = guild.get_member(int(uid_str))
        if not m:
            continue
        if not any(r.id in FACTION_ROLE_IDS for r in m.roles):
            continue
        faction_members.append((uid_str, u, m))

    faction_members.sort(
        key=lambda x: (x[1].get("level", 0), x[1].get("xp", 0)),
        reverse=True
    )
    faction_lines = []
    for i, (uid_str, u, m) in enumerate(faction_members[:10]):
        rank = medals[i] if i < 3 else f"`#{i+1}`"
        faction_lines.append(f"{rank} **{m.display_name}** — Niv. {u.get('level', 0)}")
    top_faction = "\n".join(faction_lines) or "_Aucun membre faction_"

    embed = discord.Embed(title="🏆 Classements — La Mystic", color=0xF1C40F, timestamp=now_utc())
    embed.add_field(name="━━━━━━━━━━━━━━━━━━\n📊 Top Messages",  value=top_msg,     inline=False)
    embed.add_field(name="━━━━━━━━━━━━━━━━━━\n⭐ Top Niveau",    value=top_lvl,     inline=False)
    embed.add_field(name="━━━━━━━━━━━━━━━━━━\n🎤 Top Vocal",     value=top_vc,      inline=False)
    embed.add_field(name="━━━━━━━━━━━━━━━━━━\n⚔️ Top Faction",   value=top_faction, inline=False)
    embed.set_footer(text="Top 10 par catégorie • Temps vocal live inclus")
    await ctx.send(embed=embed)


# ═══════════════════════════════════════════════════════════════
#  PATCH CHECK GLOBAL — pendu/devine/mot exemptés
# ═══════════════════════════════════════════════════════════════
# Surcharge du check global pour autoriser pendu/devine/mot partout
_original_check = bot.checks[0] if bot.checks else None

@bot.check
async def check_command_channel_v2(ctx: commands.Context) -> bool:
    # Commandes pendu exemptées du check salon
    if ctx.command and ctx.command.name in ("pendu", "devine", "mot", "pileouface", "level", "lvl", "xp", "classement", "top", "leaderboard", "giveaway", "gw"):
        return True
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


TOKEN = os.environ.get("DISCORD_TOKEN")
bot.run(TOKEN)
