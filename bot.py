import discord
from discord.ext import commands
import asyncio
import io
import os
from datetime import datetime

intents = discord.Intents.all()
bot = commands.Bot(command_prefix="!", intents=intents)

ROLE_ID        = 913064374590140417
CATEGORY_ID    = 1419109736091095090
ROLE_AUTORISE  = 703339900929441803
LOG_CHANNEL_ID = 713166766229946418

# ── Roster ──────────────────────────────────
ROSTER_CHANNEL_ID = 840695680288423976
ROSTER_ROLES = [
    (706808147796426783, "👑 Leader"),
    (703344242017173524, "⚔️ Officier"),
    (703339574515990549, "🛡️ Membre de confiance"),
    (722074234611826809, "⭐ Membre +"),
    (703339648591855656, "🔹 Membre"),
    (739879603497336928, "🌱 Recrue"),
]


# ─────────────────────────────────────────────
#  UTILITAIRE : génération du transcript HTML
# ─────────────────────────────────────────────
async def generate_transcript(channel: discord.TextChannel) -> str:
    messages = []
    async for msg in channel.history(limit=None, oldest_first=True):
        ts      = msg.created_at.strftime("%d/%m/%Y %H:%M:%S")
        author  = discord.utils.escape_markdown(str(msg.author))
        content = msg.content.replace("<", "&lt;").replace(">", "&gt;") or "<em>embed / fichier</em>"
        messages.append(
            f'<tr><td class="ts">{ts}</td><td class="author">{author}</td><td>{content}</td></tr>'
        )
    rows = "\n".join(messages)
    html = f"""<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="UTF-8">
<title>Transcript – {channel.name}</title>
<style>
  body   {{ font-family: Arial, sans-serif; background: #1e1e2e; color: #cdd6f4; padding: 20px; }}
  h1     {{ color: #cba6f7; }}
  table  {{ width: 100%; border-collapse: collapse; margin-top: 16px; }}
  th     {{ background: #313244; color: #89b4fa; padding: 8px 12px; text-align: left; }}
  td     {{ padding: 6px 12px; border-bottom: 1px solid #313244; vertical-align: top; }}
  .ts    {{ color: #a6adc8; white-space: nowrap; width: 160px; }}
  .author{{ color: #f38ba8; white-space: nowrap; width: 180px; }}
</style>
</head>
<body>
<h1>📄 Transcript – #{channel.name}</h1>
<p>Généré le {datetime.utcnow().strftime("%d/%m/%Y à %H:%M UTC")}</p>
<table>
  <thead><tr><th>Horodatage</th><th>Auteur</th><th>Message</th></tr></thead>
  <tbody>
{rows}
  </tbody>
</table>
</body>
</html>"""
    return html


# ─────────────────────────────────────────────
#  UTILITAIRE : envoi du log dans le salon staff
# ─────────────────────────────────────────────
async def send_log(guild: discord.Guild, ticket_channel: discord.TextChannel, closer: discord.Member):
    try:
        log_channel = guild.get_channel(LOG_CHANNEL_ID) or await guild.fetch_channel(LOG_CHANNEL_ID)
    except Exception as e:
        print(f"[LOG] Impossible de trouver le salon de logs ({LOG_CHANNEL_ID}) : {e}")
        return

    html     = await generate_transcript(ticket_channel)
    filename = f"transcript-{ticket_channel.name}.html"
    file     = discord.File(fp=io.BytesIO(html.encode("utf-8")), filename=filename)

    embed = discord.Embed(
        title="📁 Ticket fermé",
        color=0x9B59B6,
        timestamp=datetime.utcnow()
    )
    embed.add_field(name="🎫 Ticket",    value=ticket_channel.name,  inline=True)
    embed.add_field(name="👤 Fermé par", value=closer.mention,        inline=True)
    embed.add_field(
        name="🕐 Date",
        value=discord.utils.format_dt(datetime.utcnow(), style="F"),
        inline=True
    )
    embed.set_footer(text=f"ID salon : {ticket_channel.id}")

    try:
        await log_channel.send(embed=embed, file=file)
        print(f"[LOG] Log envoyé : {ticket_channel.name} fermé par {closer}")
    except Exception as e:
        print(f"[LOG] Erreur envoi log : {e}")


# ─────────────────────────────────────────────
#  UTILITAIRE : construction de l'embed roster
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

    embed = discord.Embed(
        title="📋 Roster — La Mystic",
        color=0x9B59B6,
        timestamp=datetime.utcnow()
    )

    total = 0
    for rid, label in ROSTER_ROLES:
        members = categories[rid]
        total  += len(members)
        if members:
            embed.add_field(
                name=f"{label} ({len(members)})",
                value="\n".join(members),
                inline=False
            )

    embed.set_footer(text=f"Total : {total} membres")
    return embed


# ─────────────────────────────────────────────
#  VUE : boutons d'ouverture de ticket
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


# ─────────────────────────────────────────────
#  VUE : confirmation de fermeture
# ─────────────────────────────────────────────
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
                    description=(
                        f"Es-tu sûr de vouloir fermer ce ticket ?\n\n"
                        f"⏳ Expiration dans **{remaining}s**…"
                    ),
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
            embed = discord.Embed(
                title="⏳ Temps écoulé",
                description="Aucune action effectuée. Le ticket n'a **pas** été fermé.",
                color=0xE67E22
            )
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

        embed = discord.Embed(
            title="🔒 Fermeture en cours…",
            description="Génération du transcript puis suppression dans **5 secondes**.",
            color=0x2ECC71
        )
        await interaction.response.edit_message(embed=embed, view=self)
        await send_log(interaction.guild, interaction.channel, self.closer)
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

        embed = discord.Embed(
            title="❌ Fermeture annulée",
            description="Le ticket reste ouvert.",
            color=0x95A5A6
        )
        await interaction.response.edit_message(embed=embed, view=self)


# ─────────────────────────────────────────────
#  CRÉATION D'UN TICKET
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

    channel = await guild.create_text_channel(
        f"ticket-{interaction.user.name}",
        category=category,
        overwrites=overwrites
    )

    if type_ticket == "recrutement":
        texte = (
            f"{role.mention} | {interaction.user.mention}\n\n"
            f"📋 **FORMULAIRE DE RECRUTEMENT – LA MYSTIC**\n\n"
            f"**1️⃣ Présentation personnelle**\n"
            f"➤ Pseudo EXACT en jeu :\n"
            f"➤ Âge (minimum 14 ans) :\n"
            f"➤ Style de jeu : (PvP / Farm / Build / Polyvalent)\n"
            f"➤ Expérience en faction / Points forts :\n\n"
            f"**2️⃣ Objectifs personnels sur le serveur**\n"
            f"➤ Court terme :\n"
            f"➤ Long terme :\n\n"
            f"**3️⃣ Motivation et contribution**\n"
            f"➤ Pourquoi souhaites-tu rejoindre la Mystic ?\n"
            f"➤ Ce que tu recherches dans une faction :\n"
            f"➤ Ce que tu peux apporter à la Mystic :\n\n"
            f"**4️⃣ Historique de factions**\n"
            f"➤ Anciennes factions (si oui, lesquelles ?) :\n"
            f"➤ Raison(s) de départ :\n\n"
            f"**5️⃣ Plateforme et stuff actuel**\n"
            f"➤ Plateforme de jeu : (PlayStation / Xbox / PC / Mobile)\n"
            f"➤ Armure, armes, enchantements importants, ressources notables :\n\n"
            f"**6️⃣ Temps de jeu & disponibilités**\n"
            f"➤ Jours joués par semaine :\n"
            f"➤ Plages horaires approximatives :\n\n"
            f"**7️⃣ Auto-critique**\n"
            f"➤ Quel défaut ou point faible pourrait jouer en ta défaveur dans une faction ?\n\n"
            f"**8️⃣ Mentalité et esprit de faction**\n"
            f"➤ Comment décrirais-tu le membre idéal d'une faction ?\n"
            f"➤ Quelle est ta vision du travail d'équipe ?\n\n"
            f"**9️⃣ Informations complémentaires**\n"
            f"➤ Screenshots OBLIGATOIRES : (stuff, métiers, argent…)\n"
            f"➤ Autres informations importantes :\n\n"
            f"**✅ Confirmation**\n"
            f"☐ J'ai 14 ans ou plus\n"
            f"☐ Je m'engage à respecter les règles de la Mystic\n"
            f"☐ Je comprends que toute fausse information entraînera un refus"
        )
    else:
        texte = (
            f"{role.mention} | {interaction.user.mention}\n\n"
            f"📩 **Autre demande**\n\n"
            f"Explique ta demande et un membre de **La Mystic** te répondra rapidement.\n"
            f"Pour fermer le ticket, tape `!fermer`."
        )

    await channel.send(texte)
    await interaction.response.send_message(
        f"✅ Ton ticket a été créé : {channel.mention}",
        ephemeral=True
    )


# ─────────────────────────────────────────────
#  COMMANDES
# ─────────────────────────────────────────────
@bot.command()
async def ticket(ctx):
    role_autorise = ctx.guild.get_role(ROLE_AUTORISE)
    if role_autorise not in ctx.author.roles:
        await ctx.send("❌ Tu n'as pas la permission d'utiliser cette commande.", delete_after=5)
        return
    embed = discord.Embed(
        title="🎫 Ouvrir un ticket",
        description="Choisis le type de demande :",
        color=0x9B59B6
    )
    await ctx.send(embed=embed, view=TicketView())


@bot.command()
async def fermer(ctx):
    if "ticket-" not in ctx.channel.name:
        await ctx.send("❌ Cette commande ne peut être utilisée que dans un ticket.", delete_after=5)
        return

    view = FermerView(closer=ctx.author)
    embed = discord.Embed(
        title="🔒 Fermer le ticket",
        description=(
            "Es-tu sûr de vouloir fermer ce ticket ?\n\n"
            "⏳ Expiration dans **30s**…"
        ),
        color=0xFF0000
    )
    embed.set_footer(text="Aucune action = ticket conservé")
    msg = await ctx.send(embed=embed, view=view)
    asyncio.create_task(view.update_countdown(msg))
    await view.wait()


@bot.command()
async def roster(ctx):
    if not ctx.author.guild_permissions.manage_guild:
        await ctx.send("❌ Tu n'as pas la permission.", delete_after=5)
        return

    try:
        channel = ctx.guild.get_channel(ROSTER_CHANNEL_ID) or await ctx.guild.fetch_channel(ROSTER_CHANNEL_ID)
    except Exception:
        await ctx.send("❌ Salon roster introuvable.", delete_after=5)
        return

    embed = build_roster_embed(ctx.guild)

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
#  EVENTS
# ─────────────────────────────────────────────
@bot.event
async def on_ready():
    print(f"✅ Mystic Bot connecté : {bot.user}")
    print(f"   LOG_CHANNEL_ID    = {LOG_CHANNEL_ID}")
    print(f"   ROSTER_CHANNEL_ID = {ROSTER_CHANNEL_ID}")


@bot.event
async def on_member_update(before: discord.Member, after: discord.Member):
    """Met à jour le roster automatiquement si un rôle roster change."""
    roster_role_ids = {r[0] for r in ROSTER_ROLES}
    before_ids = {r.id for r in before.roles}
    after_ids  = {r.id for r in after.roles}

    if before_ids & roster_role_ids == after_ids & roster_role_ids:
        return

    try:
        channel = after.guild.get_channel(ROSTER_CHANNEL_ID) or await after.guild.fetch_channel(ROSTER_CHANNEL_ID)
    except Exception:
        return

    embed = build_roster_embed(after.guild)

    async for msg in channel.history(limit=20):
        if msg.author == bot.user and msg.embeds:
            await msg.edit(embed=embed)
            return

    await channel.send(embed=embed)


# ─────────────────────────────────────────────
#  DÉMARRAGE
# ─────────────────────────────────────────────
TOKEN = os.environ.get("DISCORD_TOKEN")
bot.run(TOKEN)
