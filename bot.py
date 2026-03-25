import discord
from discord.ext import commands
import asyncio
import io
import os
import re
from datetime import datetime, timezone
from collections import defaultdict

intents = discord.Intents.all()
bot = commands.Bot(command_prefix="!", intents=intents)

# ─────────────────────────────────────────────
#  CONSTANTES
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

# Rôles staff (Officier et au-dessus) — peuvent modérer
STAFF_ROLE_IDS = {706808147796426783, 703344242017173524}

# Domaines autorisés pour l'anti-liens
ALLOWED_DOMAINS = {"discord.gg", "discord.com", "tenor.com", "giphy.com"}

# Anti-spam : {user_id: [timestamps]}
spam_tracker: dict[int, list[float]] = defaultdict(list)
spam_warned:  set[int] = set()

SPAM_LIMIT    = 4   # messages
SPAM_WINDOW   = 4.0 # secondes


# ─────────────────────────────────────────────
#  UTILITAIRES GÉNÉRAUX
# ─────────────────────────────────────────────
def is_staff(member: discord.Member) -> bool:
    if member.guild_permissions.administrator:
        return True
    return any(r.id in STAFF_ROLE_IDS for r in member.roles)


async def get_log_channel(guild: discord.Guild) -> discord.TextChannel | None:
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


# ─────────────────────────────────────────────
#  UTILITAIRE : transcript HTML
# ─────────────────────────────────────────────
async def generate_transcript(channel: discord.TextChannel) -> str:
    messages = []
    async for msg in channel.history(limit=None, oldest_first=True):
        ts      = msg.created_at.strftime("%d/%m/%Y %H:%M:%S")
        author  = discord.utils.escape_markdown(str(msg.author))
        content = msg.content.replace("<", "&lt;").replace(">", "&gt;") or "<em>embed/fichier</em>"
        messages.append(
            f'<tr><td class="ts">{ts}</td><td class="author">{author}</td><td>{content}</td></tr>'
        )
    rows = "\n".join(messages)
    return f"""<!DOCTYPE html>
<html lang="fr"><head><meta charset="UTF-8"><title>Transcript – {channel.name}</title>
<style>
  body{{font-family:Arial,sans-serif;background:#1e1e2e;color:#cdd6f4;padding:20px}}
  h1{{color:#cba6f7}}table{{width:100%;border-collapse:collapse;margin-top:16px}}
  th{{background:#313244;color:#89b4fa;padding:8px 12px;text-align:left}}
  td{{padding:6px 12px;border-bottom:1px solid #313244;vertical-align:top}}
  .ts{{color:#a6adc8;white-space:nowrap;width:160px}}.author{{color:#f38ba8;white-space:nowrap;width:180px}}
</style></head><body>
<h1>📄 Transcript – #{channel.name}</h1>
<p>Généré le {datetime.utcnow().strftime("%d/%m/%Y à %H:%M UTC")}</p>
<table><thead><tr><th>Horodatage</th><th>Auteur</th><th>Message</th></tr></thead>
<tbody>{rows}</tbody></table></body></html>"""


# ─────────────────────────────────────────────
#  UTILITAIRE : log ticket
# ─────────────────────────────────────────────
async def send_ticket_log(guild, ticket_channel, closer):
    ch = await get_log_channel(guild)
    if not ch:
        return
    html     = await generate_transcript(ticket_channel)
    filename = f"transcript-{ticket_channel.name}.html"
    file     = discord.File(fp=io.BytesIO(html.encode("utf-8")), filename=filename)
    embed = discord.Embed(title="📁 Ticket fermé", color=0x9B59B6, timestamp=datetime.utcnow())
    embed.add_field(name="🎫 Ticket",    value=ticket_channel.name, inline=True)
    embed.add_field(name="👤 Fermé par", value=closer.mention,      inline=True)
    embed.add_field(name="🕐 Date",      value=now_str(),            inline=True)
    embed.set_footer(text=f"ID salon : {ticket_channel.id}")
    try:
        await ch.send(embed=embed, file=file)
    except Exception as e:
        print(f"[LOG] Erreur ticket log : {e}")


# ─────────────────────────────────────────────
#  UTILITAIRE : roster
# ─────────────────────────────────────────────
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
    embed = discord.Embed(title="📋 Roster — La Mystic", color=0x9B59B6, timestamp=datetime.utcnow())
    total = 0
    for rid, label in ROSTER_ROLES:
        members = categories[rid]
        total  += len(members)
        if members:
            embed.add_field(name=f"{label} ({len(members)})", value="\n".join(members), inline=False)
    embed.set_footer(text=f"Total : {total} membres")
    return embed


# ─────────────────────────────────────────────
#  VUES : tickets
# ─────────────────────────────────────────────
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
        self.closer       = closer
        self.action_taken = False
        self._msg: discord.Message = None

    async def update_countdown(self, message: discord.Message):
        self._msg = message
        for remaining in range(29, 0, -1):
            if self.action_taken:
                return
            await asyncio.sleep(1)
            try:
                embed = discord.Embed(
                    title="🔒 Fermer le ticket",
                    description=f"Es-tu sûr de vouloir fermer ce ticket ?\n\n⏳ Expiration dans **{remaining}s**…",
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
            except (discord.NotFound, discord.HTTPException):
                pass

    def _disable_all(self):
        for child in self.children:
            child.disabled = True

    @discord.ui.button(label="✅ Confirmer la fermeture", style=discord.ButtonStyle.red)
    async def confirmer(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.action_taken:
            await interaction.response.send_message("⚠️ Action déjà effectuée.", ephemeral=True)
            return
        self.action_taken = True
        self._disable_all()
        self.stop()
        embed = discord.Embed(title="🔒 Fermeture en cours…", description="Génération du transcript puis suppression dans **5 secondes**.", color=0x2ECC71)
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
            await interaction.response.send_message("⚠️ Action déjà effectuée.", ephemeral=True)
            return
        self.action_taken = True
        self._disable_all()
        self.stop()
        embed = discord.Embed(title="❌ Fermeture annulée", description="Le ticket reste ouvert.", color=0x95A5A6)
        await interaction.response.edit_message(embed=embed, view=self)


# ─────────────────────────────────────────────
#  CRÉATION DE TICKET
# ─────────────────────────────────────────────
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
            f"**1️⃣ Présentation personnelle**\n➤ Pseudo EXACT en jeu :\n➤ Âge (minimum 14 ans) :\n➤ Style de jeu : (PvP / Farm / Build / Polyvalent)\n➤ Expérience en faction / Points forts :\n\n"
            f"**2️⃣ Objectifs personnels sur le serveur**\n➤ Court terme :\n➤ Long terme :\n\n"
            f"**3️⃣ Motivation et contribution**\n➤ Pourquoi souhaites-tu rejoindre la Mystic ?\n➤ Ce que tu recherches dans une faction :\n➤ Ce que tu peux apporter à la Mystic :\n\n"
            f"**4️⃣ Historique de factions**\n➤ Anciennes factions (si oui, lesquelles ?) :\n➤ Raison(s) de départ :\n\n"
            f"**5️⃣ Plateforme et stuff actuel**\n➤ Plateforme de jeu : (PlayStation / Xbox / PC / Mobile)\n➤ Armure, armes, enchantements importants, ressources notables :\n\n"
            f"**6️⃣ Temps de jeu & disponibilités**\n➤ Jours joués par semaine :\n➤ Plages horaires approximatives :\n\n"
            f"**7️⃣ Auto-critique**\n➤ Quel défaut ou point faible pourrait jouer en ta défaveur dans une faction ?\n\n"
            f"**8️⃣ Mentalité et esprit de faction**\n➤ Comment décrirais-tu le membre idéal d'une faction ?\n➤ Quelle est ta vision du travail d'équipe ?\n\n"
            f"**9️⃣ Informations complémentaires**\n➤ Screenshots OBLIGATOIRES : (stuff, métiers, argent…)\n➤ Autres informations importantes :\n\n"
            f"**✅ Confirmation**\n☐ J'ai 14 ans ou plus\n☐ Je m'engage à respecter les règles de la Mystic\n☐ Je comprends que toute fausse information entraînera un refus"
        )
    else:
        texte = f"{role.mention} | {interaction.user.mention}\n\n📩 **Autre demande**\n\nExplique ta demande et un membre de **La Mystic** te répondra rapidement.\nPour fermer le ticket, tape `!fermer`."
    await channel.send(texte)
    await interaction.response.send_message(f"✅ Ton ticket a été créé : {channel.mention}", ephemeral=True)


# ─────────────────────────────────────────────
#  COMMANDES : TICKETS
# ─────────────────────────────────────────────
@bot.command()
async def ticket(ctx):
    role_autorise = ctx.guild.get_role(ROLE_AUTORISE)
    if role_autorise not in ctx.author.roles:
        await ctx.send("❌ Tu n'as pas la permission d'utiliser cette commande.", delete_after=5)
        return
    embed = discord.Embed(title="🎫 Ouvrir un ticket", description="Choisis le type de demande :", color=0x9B59B6)
    await ctx.send(embed=embed, view=TicketView())


@bot.command()
async def fermer(ctx):
    if "ticket-" not in ctx.channel.name:
        await ctx.send("❌ Cette commande ne peut être utilisée que dans un ticket.", delete_after=5)
        return
    view  = FermerView(closer=ctx.author)
    embed = discord.Embed(title="🔒 Fermer le ticket", description="Es-tu sûr de vouloir fermer ce ticket ?\n\n⏳ Expiration dans **30s**…", color=0xFF0000)
    embed.set_footer(text="Aucune action = ticket conservé")
    msg = await ctx.send(embed=embed, view=view)
    asyncio.create_task(view.update_countdown(msg))
    await view.wait()


# ─────────────────────────────────────────────
#  COMMANDES : ROSTER
# ─────────────────────────────────────────────
@bot.command()
async def roster(ctx):
    if not is_staff(ctx.author):
        await ctx.send("❌ Tu n'as pas la permission.", delete_after=5)
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


# ─────────────────────────────────────────────
#  COMMANDES : MODÉRATION
# ─────────────────────────────────────────────
def mod_check(ctx) -> bool:
    return is_staff(ctx.author)


@bot.command()
async def ban(ctx, member: discord.Member, *, reason: str = "Aucune raison fournie"):
    if not mod_check(ctx):
        await ctx.send("❌ Permission refusée.", delete_after=5); return
    if not ctx.guild.me.guild_permissions.ban_members:
        await ctx.send("❌ Je n'ai pas la permission de bannir.", delete_after=5); return
    await member.ban(reason=reason, delete_message_days=1)
    await ctx.send(f"🔨 **{member}** a été banni. Raison : {reason}")
    embed = discord.Embed(title="🔨 Ban", color=0xE74C3C, timestamp=datetime.utcnow())
    embed.add_field(name="👤 Membre",   value=f"{member} ({member.id})", inline=True)
    embed.add_field(name="🛡️ Modérateur", value=ctx.author.mention,     inline=True)
    embed.add_field(name="📝 Raison",   value=reason,                   inline=False)
    embed.add_field(name="🕐 Date",     value=now_str(),                 inline=False)
    await send_log(ctx.guild, embed)


@bot.command()
async def banip(ctx, member: discord.Member, *, reason: str = "Ban renforcé"):
    if not mod_check(ctx):
        await ctx.send("❌ Permission refusée.", delete_after=5); return
    if not ctx.guild.me.guild_permissions.ban_members:
        await ctx.send("❌ Je n'ai pas la permission de bannir.", delete_after=5); return
    # Ban + suppression de 7 jours de messages + note anti-alt
    await member.ban(reason=f"[BAN RENFORCÉ] {reason}", delete_message_days=7)
    await ctx.send(
        f"🔨 **{member}** a été banni de façon renforcée.\n"
        f"⚠️ *Note : Discord ne permet pas un vrai ban IP. "
        f"Active la vérification de téléphone dans les paramètres du serveur pour limiter les alts.*"
    )
    embed = discord.Embed(title="🔨 Ban Renforcé (BanIP)", color=0xC0392B, timestamp=datetime.utcnow())
    embed.add_field(name="👤 Membre",      value=f"{member} ({member.id})", inline=True)
    embed.add_field(name="🛡️ Modérateur",  value=ctx.author.mention,       inline=True)
    embed.add_field(name="📝 Raison",      value=reason,                   inline=False)
    embed.add_field(name="🗑️ Messages",    value="7 jours supprimés",      inline=True)
    embed.add_field(name="🕐 Date",        value=now_str(),                 inline=False)
    await send_log(ctx.guild, embed)


@bot.command()
async def kick(ctx, member: discord.Member, *, reason: str = "Aucune raison fournie"):
    if not mod_check(ctx):
        await ctx.send("❌ Permission refusée.", delete_after=5); return
    if not ctx.guild.me.guild_permissions.kick_members:
        await ctx.send("❌ Je n'ai pas la permission de kick.", delete_after=5); return
    await member.kick(reason=reason)
    await ctx.send(f"👢 **{member}** a été expulsé. Raison : {reason}")
    embed = discord.Embed(title="👢 Kick", color=0xE67E22, timestamp=datetime.utcnow())
    embed.add_field(name="👤 Membre",     value=f"{member} ({member.id})", inline=True)
    embed.add_field(name="🛡️ Modérateur", value=ctx.author.mention,       inline=True)
    embed.add_field(name="📝 Raison",     value=reason,                   inline=False)
    embed.add_field(name="🕐 Date",       value=now_str(),                 inline=False)
    await send_log(ctx.guild, embed)


@bot.command()
async def mute(ctx, member: discord.Member, *, reason: str = "Aucune raison fournie"):
    if not mod_check(ctx):
        await ctx.send("❌ Permission refusée.", delete_after=5); return
    mute_role = discord.utils.get(ctx.guild.roles, name="Muted")
    if not mute_role:
        # Crée le rôle Muted automatiquement s'il n'existe pas
        mute_role = await ctx.guild.create_role(name="Muted", reason="Création auto du rôle Muted")
        for channel in ctx.guild.channels:
            await channel.set_permissions(mute_role, send_messages=False, speak=False)
    await member.add_roles(mute_role, reason=reason)
    await ctx.send(f"🔇 **{member}** a été mute. Raison : {reason}")
    embed = discord.Embed(title="🔇 Mute", color=0xE67E22, timestamp=datetime.utcnow())
    embed.add_field(name="👤 Membre",     value=f"{member} ({member.id})", inline=True)
    embed.add_field(name="🛡️ Modérateur", value=ctx.author.mention,       inline=True)
    embed.add_field(name="📝 Raison",     value=reason,                   inline=False)
    embed.add_field(name="🕐 Date",       value=now_str(),                 inline=False)
    await send_log(ctx.guild, embed)


@bot.command()
async def unmute(ctx, member: discord.Member):
    if not mod_check(ctx):
        await ctx.send("❌ Permission refusée.", delete_after=5); return
    mute_role = discord.utils.get(ctx.guild.roles, name="Muted")
    if not mute_role or mute_role not in member.roles:
        await ctx.send("✅ Ce membre n'est pas mute.", delete_after=5); return
    await member.remove_roles(mute_role)
    await ctx.send(f"🔊 **{member}** a été unmute.")
    embed = discord.Embed(title="🔊 Unmute", color=0x2ECC71, timestamp=datetime.utcnow())
    embed.add_field(name="👤 Membre",     value=f"{member} ({member.id})", inline=True)
    embed.add_field(name="🛡️ Modérateur", value=ctx.author.mention,       inline=True)
    embed.add_field(name="🕐 Date",       value=now_str(),                 inline=False)
    await send_log(ctx.guild, embed)


@bot.command()
async def effacer(ctx, nombre: int):
    if not mod_check(ctx):
        await ctx.send("❌ Permission refusée.", delete_after=5); return
    if nombre < 1 or nombre > 100:
        await ctx.send("❌ Entre un nombre entre 1 et 100.", delete_after=5); return
    deleted = await ctx.channel.purge(limit=nombre + 1)
    await ctx.send(f"🗑️ **{len(deleted) - 1}** messages supprimés.", delete_after=5)
    embed = discord.Embed(title="🗑️ Purge", color=0x95A5A6, timestamp=datetime.utcnow())
    embed.add_field(name="🛡️ Modérateur",  value=ctx.author.mention,     inline=True)
    embed.add_field(name="📍 Salon",        value=ctx.channel.mention,    inline=True)
    embed.add_field(name="🗑️ Supprimés",   value=str(len(deleted) - 1),  inline=True)
    embed.add_field(name="🕐 Date",         value=now_str(),              inline=False)
    await send_log(ctx.guild, embed)


# ─────────────────────────────────────────────
#  COMMANDE : INFO
# ─────────────────────────────────────────────
@bot.command()
async def info(ctx, member: discord.Member = None):
    member = member or ctx.author
    roles  = [r.mention for r in reversed(member.roles) if r.name != "@everyone"]
    top_role = member.top_role.mention if member.top_role.name != "@everyone" else "Aucun"

    perms = []
    if member.guild_permissions.administrator:     perms.append("👑 Administrateur")
    if member.guild_permissions.manage_guild:      perms.append("⚙️ Gérer le serveur")
    if member.guild_permissions.ban_members:       perms.append("🔨 Bannir")
    if member.guild_permissions.kick_members:      perms.append("👢 Expulser")
    if member.guild_permissions.manage_messages:   perms.append("🗑️ Gérer messages")
    if member.guild_permissions.manage_roles:      perms.append("🎭 Gérer rôles")

    status_map = {
        discord.Status.online:    "🟢 En ligne",
        discord.Status.idle:      "🟡 Absent",
        discord.Status.dnd:       "🔴 Ne pas déranger",
        discord.Status.offline:   "⚫ Hors ligne",
    }
    status = status_map.get(member.status, "⚫ Inconnu")

    activity = "Aucune"
    if member.activity:
        if isinstance(member.activity, discord.Game):
            activity = f"🎮 Joue à {member.activity.name}"
        elif isinstance(member.activity, discord.Streaming):
            activity = f"📺 Stream : {member.activity.name}"
        elif isinstance(member.activity, discord.CustomActivity):
            activity = f"💬 {member.activity.name}"
        else:
            activity = member.activity.name

    embed = discord.Embed(
        title=f"👤 Informations — {member.display_name}",
        color=member.color if member.color != discord.Color.default() else 0x3498DB,
        timestamp=datetime.utcnow()
    )
    embed.set_thumbnail(url=member.display_avatar.url)
    if member.banner:
        embed.set_image(url=member.banner.url)

    embed.add_field(name="📛 Pseudo",         value=member.display_name,                         inline=True)
    embed.add_field(name="🏷️ Tag",            value=str(member),                                 inline=True)
    embed.add_field(name="🤖 Bot",             value="✅ Oui" if member.bot else "❌ Non",        inline=True)
    embed.add_field(name="🆔 ID",              value=str(member.id),                             inline=True)
    embed.add_field(name="📅 Compte créé",     value=discord.utils.format_dt(member.created_at, style="D"), inline=True)
    embed.add_field(name="📥 Arrivée serveur", value=discord.utils.format_dt(member.joined_at, style="D") if member.joined_at else "Inconnu", inline=True)
    embed.add_field(name="📶 Statut",          value=status,                                     inline=True)
    embed.add_field(name="🎯 Activité",        value=activity,                                   inline=True)
    embed.add_field(name="🎖️ Rôle principal",  value=top_role,                                   inline=True)
    embed.add_field(name=f"🎭 Rôles ({len(roles)})", value=", ".join(roles[:20]) or "Aucun",    inline=False)
    embed.add_field(name="🔑 Permissions clés", value=", ".join(perms) or "Aucune",              inline=False)
    embed.set_footer(text=f"Demandé par {ctx.author}")
    await ctx.send(embed=embed)


# ─────────────────────────────────────────────
#  EVENTS : ANTI-SPAM
# ─────────────────────────────────────────────
@bot.event
async def on_message(message: discord.Message):
    if message.author.bot or not message.guild:
        await bot.process_commands(message)
        return

    member = message.author

    # ── Anti-liens ──────────────────────────
    url_pattern = re.compile(r"https?://|www\.", re.IGNORECASE)
    if url_pattern.search(message.content):
        if not member.guild_permissions.administrator:
            # Vérifie si le domaine est dans la whitelist
            domain_match = re.search(r"(?:https?://|www\.)([^/\s]+)", message.content, re.IGNORECASE)
            domain = domain_match.group(1).lower() if domain_match else ""
            if not any(domain.endswith(d) for d in ALLOWED_DOMAINS):
                await message.delete()
                warn = await message.channel.send(
                    f"🔗 {member.mention} Les liens ne sont pas autorisés ici.", delete_after=5
                )
                embed = discord.Embed(title="🔗 Lien supprimé", color=0x95A5A6, timestamp=datetime.utcnow())
                embed.add_field(name="👤 Auteur",  value=f"{member} ({member.id})", inline=True)
                embed.add_field(name="📍 Salon",   value=message.channel.mention,   inline=True)
                embed.add_field(name="💬 Contenu", value=message.content[:500],     inline=False)
                embed.add_field(name="🕐 Date",    value=now_str(),                  inline=False)
                await send_log(message.guild, embed)
                return

    # ── Anti-spam ───────────────────────────
    if not is_staff(member):
        now = asyncio.get_event_loop().time()
        timestamps = spam_tracker[member.id]
        timestamps.append(now)
        # Garde uniquement les timestamps dans la fenêtre
        spam_tracker[member.id] = [t for t in timestamps if now - t < SPAM_WINDOW]

        if len(spam_tracker[member.id]) > SPAM_LIMIT:
            if member.id in spam_warned:
                # Deuxième infraction → kick
                spam_warned.discard(member.id)
                spam_tracker.pop(member.id, None)
                try:
                    await member.kick(reason="Anti-spam automatique")
                    await message.channel.send(
                        f"🚫 {member.mention} a été expulsé pour spam répété.", delete_after=10
                    )
                    embed = discord.Embed(title="🚫 Kick Anti-Spam", color=0xE74C3C, timestamp=datetime.utcnow())
                    embed.add_field(name="👤 Membre", value=f"{member} ({member.id})", inline=True)
                    embed.add_field(name="📍 Salon",  value=message.channel.mention,   inline=True)
                    embed.add_field(name="🕐 Date",   value=now_str(),                  inline=False)
                    await send_log(message.guild, embed)
                except discord.Forbidden:
                    pass
            else:
                # Première infraction → avertissement
                spam_warned.add(member.id)
                spam_tracker[member.id] = []
                await message.channel.send(
                    f"⚠️ {member.mention} Stop le spam ! Prochain spam = **kick automatique**.", delete_after=8
                )

    await bot.process_commands(message)


# ─────────────────────────────────────────────
#  EVENTS : LOGS AUTO
# ─────────────────────────────────────────────
@bot.event
async def on_message_delete(message: discord.Message):
    if message.author.bot or not message.guild:
        return
    embed = discord.Embed(title="🗑️ Message supprimé", color=0x95A5A6, timestamp=datetime.utcnow())
    embed.add_field(name="👤 Auteur",   value=f"{message.author} ({message.author.id})", inline=True)
    embed.add_field(name="📍 Salon",    value=message.channel.mention,                   inline=True)
    embed.add_field(name="💬 Contenu",  value=message.content[:1000] or "<vide>",        inline=False)
    embed.add_field(name="🆔 Msg ID",   value=str(message.id),                           inline=True)
    embed.add_field(name="🕐 Date",     value=now_str(),                                  inline=False)
    await send_log(message.guild, embed)


@bot.event
async def on_message_edit(before: discord.Message, after: discord.Message):
    if before.author.bot or not before.guild or before.content == after.content:
        return
    embed = discord.Embed(title="✏️ Message modifié", color=0x3498DB, timestamp=datetime.utcnow())
    embed.add_field(name="👤 Auteur",       value=f"{before.author} ({before.author.id})", inline=True)
    embed.add_field(name="📍 Salon",        value=before.channel.mention,                  inline=True)
    embed.add_field(name="📝 Avant",        value=before.content[:500] or "<vide>",        inline=False)
    embed.add_field(name="📝 Après",        value=after.content[:500] or "<vide>",         inline=False)
    embed.add_field(name="🔗 Lien",         value=f"[Voir le message]({after.jump_url})",  inline=True)
    embed.add_field(name="🕐 Date",         value=now_str(),                               inline=False)
    await send_log(before.guild, embed)


@bot.event
async def on_member_join(member: discord.Member):
    embed = discord.Embed(title="📥 Membre arrivé", color=0x2ECC71, timestamp=datetime.utcnow())
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.add_field(name="👤 Membre",        value=f"{member} ({member.id})", inline=True)
    embed.add_field(name="📅 Compte créé",   value=discord.utils.format_dt(member.created_at, style="D"), inline=True)
    embed.add_field(name="👥 Membres total", value=str(member.guild.member_count), inline=True)
    await send_log(member.guild, embed)


@bot.event
async def on_member_remove(member: discord.Member):
    embed = discord.Embed(title="📤 Membre parti", color=0xE74C3C, timestamp=datetime.utcnow())
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.add_field(name="👤 Membre",        value=f"{member} ({member.id})", inline=True)
    embed.add_field(name="👥 Membres total", value=str(member.guild.member_count), inline=True)
    await send_log(member.guild, embed)


@bot.event
async def on_member_update(before: discord.Member, after: discord.Member):
    # ── Roster auto ──
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

    # ── Log changement de rôles ──
    added   = set(after.roles)  - set(before.roles)
    removed = set(before.roles) - set(after.roles)
    if added or removed:
        embed = discord.Embed(title="🎭 Rôles modifiés", color=0x9B59B6, timestamp=datetime.utcnow())
        embed.add_field(name="👤 Membre",    value=f"{after} ({after.id})", inline=True)
        if added:
            embed.add_field(name="✅ Ajoutés",   value=", ".join(r.mention for r in added),   inline=False)
        if removed:
            embed.add_field(name="❌ Retirés",   value=", ".join(r.mention for r in removed), inline=False)
        embed.add_field(name="🕐 Date", value=now_str(), inline=False)
        await send_log(after.guild, embed)


# ─────────────────────────────────────────────
#  DÉMARRAGE
# ─────────────────────────────────────────────
@bot.event
async def on_ready():
    print(f"✅ Mystic Bot connecté : {bot.user}")
    print(f"   LOG_CHANNEL_ID    = {LOG_CHANNEL_ID}")
    print(f"   ROSTER_CHANNEL_ID = {ROSTER_CHANNEL_ID}")


TOKEN = os.environ.get("DISCORD_TOKEN")
bot.run(TOKEN)