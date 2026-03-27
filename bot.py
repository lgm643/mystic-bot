import discord
from discord.ext import commands
import asyncio
import io
import os
import re
import time
import json
import random
import math
from datetime import datetime, timezone
from collections import defaultdict
from pathlib import Path

intents = discord.Intents.all()
bot = commands.Bot(command_prefix="!", intents=intents)

# ─────────────────────────────────────────────
#  CONSTANTES GÉNÉRALES
# ─────────────────────────────────────────────
ROLE_ID             = 913064374590140417
CATEGORY_ID         = 1419109736091095090
ROLE_AUTORISE       = 703339900929441803
LOG_CHANNEL_ID      = 713166766229946418
ROSTER_CHANNEL_ID   = 840695680288423976
WELCOME_CHANNEL_ID  = 744856318971740182
VISITOR_ROLE_NAME   = "visiteur"

ROSTER_ROLES = [
    (706808147796426783, "👑 Leader"),
    (703344242017173524, "⚔️ Officier"),
    (703339574515990549, "🛡️ Membre de confiance"),
    (722074234611826809, "⭐ Membre +"),
    (703339648591855656, "🔹 Membre"),
    (739879603497336928, "🌱 Recrue"),
]

STAFF_ROLE_IDS    = {706808147796426783, 703344242017173524}
GIVEAWAY_ROLE_IDS = {706808147796426783, 703344242017173524}
FACTION_ROLE_IDS  = {
    739879603497336928, 703339648591855656, 722074234611826809,
    703339574515990549, 703344242017173524, 706808147796426783,
}

ALLOWED_DOMAINS      = {"tenor.com", "giphy.com"}
ALLOWED_CMD_CHANNELS = {703342923634180137, 703349716183941162}

SPAM_LIMIT  = 4
SPAM_WINDOW = 6.0
spam_tracker: dict[int, list[float]] = defaultdict(list)
spam_warned:  set[int] = set()

DATA_FILE  = "user_data.json"
GAMES_FILE = "games_data.json"

xp_cooldowns: dict[int, float] = {}

EXEMPT_COMMANDS = {
    "pendu", "devine", "mot", "pileouface", "pendustop",
    "morpion", "morpionstop",
    "level", "lvl", "xp",
    "classement", "top", "leaderboard",
    "giveaway", "gw",
    "pub",
    "help", "aide", "commandes",
    "info",
}

active_pendu:    dict[int, dict] = {}
active_morpion:  dict[int, dict] = {}
pendu_tasks:     dict[int, asyncio.Task] = {}
morpion_tasks:   dict[int, asyncio.Task] = {}
active_giveaways: dict[int, dict] = {}


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


def fmt_voice(seconds: float) -> str:
    seconds = int(seconds)
    h, r = divmod(seconds, 3600)
    m, s = divmod(r, 60)
    if h: return f"{h}h {m}m"
    if m: return f"{m}m {s}s"
    return f"{s}s"


# ─────────────────────────────────────────────
#  CHECK GLOBAL
# ─────────────────────────────────────────────
@bot.check
async def check_command_channel(ctx: commands.Context) -> bool:
    if ctx.command and ctx.command.name in EXEMPT_COMMANDS:
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


# ═══════════════════════════════════════════════════════════════
#  DONNÉES UTILISATEURS (XP)
# ═══════════════════════════════════════════════════════════════
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
            "xp": 0, "level": 0,
            "message_count": 0,
            "voice_time": 0.0,
            "voice_join": None,
        }
    return data[uid]


def xp_for_level(level: int) -> int:
    return 100 * (level + 1) + 50 * level * level


def progress_bar(current: int, total: int, length: int = 10) -> str:
    filled = int(length * current / total) if total > 0 else 0
    return "█" * filled + "░" * (length - filled)


# ═══════════════════════════════════════════════════════════════
#  DONNÉES PARTIES
# ═══════════════════════════════════════════════════════════════
def save_games():
    data = {}
    for ch_id, g in active_pendu.items():
        data[f"pendu_{ch_id}"] = {
            "word": g["word"], "guessed": list(g["guessed"]),
            "errors": g["errors"], "creator": g["creator"],
            "participants": g["participants"],
            "msg_id": g.get("msg_id"), "end_time": g["end_time"],
        }
    for ch_id, g in active_morpion.items():
        data[f"morpion_{ch_id}"] = {
            "board": g["board"], "players": g["players"],
            "current": g["current"], "msg_id": g.get("msg_id"),
            "end_time": g["end_time"],
        }
    try:
        with open(GAMES_FILE, "w") as f:
            json.dump(data, f)
    except Exception as e:
        print(f"[GAMES] Erreur sauvegarde : {e}")


def load_games() -> dict:
    if Path(GAMES_FILE).exists():
        try:
            with open(GAMES_FILE) as f:
                return json.load(f)
        except Exception:
            pass
    return {}


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
#  VUES TICKETS — persistent (timeout=None pour survivre au restart)
# ═══════════════════════════════════════════════════════════════
class TicketView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="📋 Demande de recrutement", style=discord.ButtonStyle.green, custom_id="ticket_recrutement")
    async def recrutement(self, interaction: discord.Interaction, button: discord.ui.Button):
        await creer_ticket(interaction, "recrutement")

    @discord.ui.button(label="📩 Autre demande", style=discord.ButtonStyle.blurple, custom_id="ticket_autre")
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

    @discord.ui.button(label="✅ Confirmer la fermeture", style=discord.ButtonStyle.red, custom_id="fermer_confirmer")
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

    @discord.ui.button(label="❌ Annuler", style=discord.ButtonStyle.grey, custom_id="fermer_annuler")
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
    if not is_staff(ctx.author): await ctx.send("❌ Permission refusée.", delete_after=5); return
    if member is None: await ctx.send("❌ `!ban @membre raison`", delete_after=5); return
    if not ctx.guild.me.guild_permissions.ban_members: await ctx.send("❌ Permission manquante.", delete_after=5); return
    try:
        await member.ban(reason=reason, delete_message_days=1)
        await ctx.send(f"🔨 **{member}** banni. Raison : {reason}")
        embed = discord.Embed(title="🔨 Ban", color=0xE74C3C, timestamp=now_utc())
        embed.add_field(name="👤 Membre",     value=f"{member} ({member.id})", inline=True)
        embed.add_field(name="🛡️ Modérateur", value=ctx.author.mention,       inline=True)
        embed.add_field(name="📝 Raison",     value=reason,                   inline=False)
        await send_log(ctx.guild, embed)
    except discord.Forbidden:
        await ctx.send("❌ Je ne peux pas bannir ce membre.", delete_after=5)


@bot.command()
async def kick(ctx, member: discord.Member = None, *, reason: str = "Aucune raison fournie"):
    if not is_staff(ctx.author): await ctx.send("❌ Permission refusée.", delete_after=5); return
    if member is None: await ctx.send("❌ `!kick @membre raison`", delete_after=5); return
    if not ctx.guild.me.guild_permissions.kick_members: await ctx.send("❌ Permission manquante.", delete_after=5); return
    try:
        await member.kick(reason=reason)
        await ctx.send(f"👢 **{member}** expulsé. Raison : {reason}")
        embed = discord.Embed(title="👢 Kick", color=0xE67E22, timestamp=now_utc())
        embed.add_field(name="👤 Membre",     value=f"{member} ({member.id})", inline=True)
        embed.add_field(name="🛡️ Modérateur", value=ctx.author.mention,       inline=True)
        embed.add_field(name="📝 Raison",     value=reason,                   inline=False)
        await send_log(ctx.guild, embed)
    except discord.Forbidden:
        await ctx.send("❌ Je ne peux pas kick ce membre.", delete_after=5)


@bot.command()
async def mute(ctx, member: discord.Member = None, *, reason: str = "Aucune raison fournie"):
    if not is_staff(ctx.author): await ctx.send("❌ Permission refusée.", delete_after=5); return
    if member is None: await ctx.send("❌ `!mute @membre raison`", delete_after=5); return
    mute_role = discord.utils.get(ctx.guild.roles, name="Muted")
    if not mute_role:
        mute_role = await ctx.guild.create_role(name="Muted", reason="Création auto")
        for ch in ctx.guild.channels:
            await ch.set_permissions(mute_role, send_messages=False, speak=False)
    await member.add_roles(mute_role, reason=reason)
    await ctx.send(f"🔇 **{member}** muté. Raison : {reason}")
    embed = discord.Embed(title="🔇 Mute", color=0xE67E22, timestamp=now_utc())
    embed.add_field(name="👤 Membre", value=f"{member} ({member.id})", inline=True)
    embed.add_field(name="🛡️ Modérateur", value=ctx.author.mention, inline=True)
    embed.add_field(name="📝 Raison", value=reason, inline=False)
    await send_log(ctx.guild, embed)


@bot.command()
async def unmute(ctx, member: discord.Member = None):
    if not is_staff(ctx.author): await ctx.send("❌ Permission refusée.", delete_after=5); return
    if member is None: await ctx.send("❌ `!unmute @membre`", delete_after=5); return
    mute_role = discord.utils.get(ctx.guild.roles, name="Muted")
    if not mute_role or mute_role not in member.roles:
        await ctx.send("✅ Ce membre n'est pas muté.", delete_after=5); return
    await member.remove_roles(mute_role)
    await ctx.send(f"🔊 **{member}** unmuté.")
    embed = discord.Embed(title="🔊 Unmute", color=0x2ECC71, timestamp=now_utc())
    embed.add_field(name="👤 Membre", value=f"{member} ({member.id})", inline=True)
    embed.add_field(name="🛡️ Modérateur", value=ctx.author.mention, inline=True)
    await send_log(ctx.guild, embed)


@bot.command()
async def effacer(ctx, nombre: int = None):
    if not is_staff(ctx.author): await ctx.send("❌ Permission refusée.", delete_after=5); return
    if nombre is None: await ctx.send("❌ `!effacer 10`", delete_after=5); return
    if nombre < 1 or nombre > 100: await ctx.send("❌ Entre 1 et 100.", delete_after=5); return
    deleted = await ctx.channel.purge(limit=nombre + 1)
    await ctx.send(f"🗑️ **{len(deleted) - 1}** messages supprimés.", delete_after=5)
    embed = discord.Embed(title="🗑️ Purge", color=0x95A5A6, timestamp=now_utc())
    embed.add_field(name="🛡️ Modérateur", value=ctx.author.mention,    inline=True)
    embed.add_field(name="📍 Salon",      value=ctx.channel.mention,   inline=True)
    embed.add_field(name="🗑️ Supprimés", value=str(len(deleted) - 1), inline=True)
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
    embed = discord.Embed(title=f"👤 {member.display_name}",
        color=member.color if member.color != discord.Color.default() else 0x3498DB, timestamp=now_utc())
    embed.set_thumbnail(url=member.display_avatar.url)
    if member.banner: embed.set_image(url=member.banner.url)
    embed.add_field(name="📛 Pseudo",         value=member.display_name, inline=True)
    embed.add_field(name="🏷️ Tag",            value=str(member),         inline=True)
    embed.add_field(name="🤖 Bot",             value="✅" if member.bot else "❌", inline=True)
    embed.add_field(name="🆔 ID",              value=str(member.id),     inline=True)
    embed.add_field(name="📅 Compte créé",     value=discord.utils.format_dt(member.created_at, style="D"), inline=True)
    embed.add_field(name="📥 Arrivée serveur", value=discord.utils.format_dt(member.joined_at, style="D") if member.joined_at else "?", inline=True)
    embed.add_field(name="📶 Statut",          value=status,   inline=True)
    embed.add_field(name="🎯 Activité",        value=activity, inline=True)
    embed.add_field(name="🎖️ Rôle principal",  value=top_role, inline=True)
    embed.add_field(name=f"🎭 Rôles ({len(roles)})", value=", ".join(roles[:20]) or "Aucun", inline=False)
    embed.add_field(name="🔑 Permissions",     value=", ".join(perms) or "Aucune", inline=False)
    embed.set_footer(text=f"Demandé par {ctx.author}")
    await ctx.send(embed=embed)


# ═══════════════════════════════════════════════════════════════
#  COMMANDE !PUB
# ═══════════════════════════════════════════════════════════════
@bot.command(name="pub")
async def pub_cmd(ctx):
    if not is_staff(ctx.author):
        await ctx.send("❌ Permission refusée.", delete_after=5)
        return
    texte = (
        "🔥 **__LA MYSTIC RECRUTE__** 🐦‍🔥🔥\n\n"
        "Vous ne savez plus quoi faire ? Envie de PvP, de farm et de domination ?\n"
        "La faction **__Mystic__** est faite pour vous !\n\n"
        "Nous recrutons des **joueurs PvP expérimentés**, des **farmeurs motivés**, "
        "mais aussi des **nouveaux joueurs** qui veulent progresser et rejoindre une faction "
        "sérieuse avec de gros projets et une vraie ambiance d'équipe.\n\n"
        "---\n\n"
        "🎯 **__AU PROGRAMME :__**\n"
        "• Base claim solide et organisée\n"
        "• Sessions PvP régulières avec toute la faction\n"
        "• Du tryhard et de la compétition\n"
        "• Farms de faction énormes accessibles à tous les membres\n"
        "• F-Home commun pour toute la faction\n"
        "• Du fun, de la bonne humeur et beaucoup de rigolade\n"
        "• Et plein d'autres projets en équipe\n\n"
        "---\n\n"
        "✏️ **__PRÉREQUIS :__**\n"
        "• Avoir Minecraft\n"
        "• Âge minimum : 15 ans\n"
        "• Bonne humeur obligatoire 😄\n"
        "• Être capable d'être en vocal pour les sessions PvP\n\n"
        "---\n\n"
        "📩 **__INTÉRESSÉ ?__**\n"
        "Le lien est dans la bio de **@lgm6143** pour rejoindre le Discord et envoyer ta candidature !\n\n"
        "---\n\n"
        "🐦‍🔥 **__MYSTIC — RISE LIKE A PHOENIX__** 🔥"
    )
    msg = await ctx.send(texte)
    await msg.reply("N'hésite pas à partager la faction et contribuer à la montée de la Mystic 🐦‍🔥")


# ═══════════════════════════════════════════════════════════════
#  ON_MESSAGE : ANTI-LIENS + ANTI-SPAM + XP
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
                try:
                    await message.delete()
                    await message.channel.send(f"❌ {member.mention} Tu n'as pas la permission d'envoyer des liens ici.", delete_after=6)
                    embed = discord.Embed(title="🔗 Lien bloqué", color=0xE74C3C, timestamp=now_utc())
                    embed.add_field(name="👤 Auteur",  value=f"{member} ({member.id})", inline=True)
                    embed.add_field(name="📍 Salon",   value=message.channel.mention,   inline=True)
                    embed.add_field(name="💬 Contenu", value=message.content[:500],     inline=False)
                    await send_log(message.guild, embed)
                except discord.Forbidden:
                    print("[ANTI-LIENS] Permission manquante")
                except Exception as e:
                    print(f"[ANTI-LIENS] Erreur : {e}")
                return

    # ── Anti-spam ──
    if not is_staff(member):
        now_m = time.monotonic()
        spam_tracker[member.id].append(now_m)
        spam_tracker[member.id] = [t for t in spam_tracker[member.id] if now_m - t <= SPAM_WINDOW]
        count = len(spam_tracker[member.id])
        if count > SPAM_LIMIT:
            if member.id in spam_warned:
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
                    pass
            else:
                spam_warned.add(member.id)
                spam_tracker[member.id] = []
                await message.channel.send(f"⚠️ {member.mention} **Stop le spam !** Prochaine fois = **expulsion automatique**.", delete_after=10)

    # ── XP (listener séparé via bot.listen) ──
    await bot.process_commands(message)


@bot.listen("on_message")
async def xp_on_message(message: discord.Message):
    if message.author.bot or not message.guild:
        return
    uid  = message.author.id
    now  = time.monotonic()
    if now - xp_cooldowns.get(uid, 0) < 10:
        return
    xp_cooldowns[uid] = now
    data = load_user_data()
    u    = get_user(data, uid)
    u["message_count"] += 1
    gained   = random.randint(5, 15)
    u["xp"] += gained
    old_level = u["level"]
    required  = xp_for_level(old_level + 1)
    if u["xp"] >= required:
        u["level"] += 1
        u["xp"]    -= required
        save_user_data(data)
        msg = await message.channel.send(f"🎉 {message.author.mention} passe niveau **{u['level']}** ! GG 🔥")
        await asyncio.sleep(2)
        try:
            await msg.delete()
        except Exception:
            pass
        return
    save_user_data(data)


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
    # ── Rôle visiteur automatique ──
    visitor_role = discord.utils.get(member.guild.roles, name=VISITOR_ROLE_NAME)
    if visitor_role:
        try:
            await member.add_roles(visitor_role, reason="Rôle visiteur automatique")
        except Exception as e:
            print(f"[WELCOME] Erreur rôle visiteur : {e}")

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


@bot.event
async def on_voice_state_update(member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
    if member.bot:
        return
    data = load_user_data()
    u    = get_user(data, member.id)
    now  = time.time()
    if before.channel is None and after.channel is not None:
        u["voice_join"] = now
    elif before.channel is not None and after.channel is None:
        if u.get("voice_join"):
            duration         = now - u["voice_join"]
            u["voice_time"] += duration
            u["voice_join"]  = None
    save_user_data(data)


# ═══════════════════════════════════════════════════════════════
#  COMMANDE LEVEL
# ═══════════════════════════════════════════════════════════════
@bot.command(name="level", aliases=["lvl", "xp"])
async def level_cmd(ctx, member: discord.Member = None):
    member   = member or ctx.author
    data     = load_user_data()
    u        = get_user(data, member.id)
    save_user_data(data)
    lvl      = u["level"]
    cur_xp   = u["xp"]
    required = xp_for_level(lvl + 1)
    bar      = progress_bar(cur_xp, required)
    embed    = discord.Embed(title=f"📊 Niveau — {member.display_name}",
        color=member.color if member.color != discord.Color.default() else 0x9B59B6, timestamp=now_utc())
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.add_field(name="🏆 Niveau",   value=str(lvl),                  inline=True)
    embed.add_field(name="✉️ Messages", value=str(u["message_count"]),    inline=True)
    embed.add_field(name="🎤 Vocal",    value=fmt_voice(u["voice_time"]), inline=True)
    embed.add_field(name=f"⭐ XP — {cur_xp}/{required}",
        value=f"`{bar}` {int(cur_xp/required*100)}%", inline=False)
    await ctx.send(embed=embed)


# ═══════════════════════════════════════════════════════════════
#  MINI-JEU : PILE OU FACE
# ═══════════════════════════════════════════════════════════════
@bot.command(name="pileouface", aliases=["pof", "coinflip"])
async def pof_cmd(ctx):
    result = random.choice(["🪙 **Pile**", "🔵 **Face**"])
    embed  = discord.Embed(title="🪙 Pile ou Face", description=f"Résultat : {result}", color=0xF1C40F)
    await ctx.send(embed=embed)


# ═══════════════════════════════════════════════════════════════
#  MINI-JEU : PENDU
# ═══════════════════════════════════════════════════════════════
PENDU_MOTS = [
    "horloge","montagne","riviere","ocean","plage","desert","foret","ile","vallee","colline",
    "nuage","orage","tempete","pluie","neige","vent","soleil","lune","etoile","ciel",
    "ami","famille","enfant","adulte","voisin","inconnu","personne","individu","groupe","equipe",
    "chef","leader","directeur","client","vendeur","acheteur","visiteur","invite","membre","participant",
    "musique","chanson","instrument","guitare","piano","batterie","violon","concert","festival","spectacle",
    "film","cinema","acteur","realisateur","scene","camera","studio","projection","serie","episode",
    "livre","roman","auteur","lecture","bibliotheque","page","chapitre","histoire","conte","poeme",
    "journal","article","magazine","publication","ecriture","stylo","papier","cahier","encre","lettre",
    "argent","banque","compte","carte","paiement","achat","vente","prix","valeur","cout",
    "economie","finance","budget","epargne","credit","depense","profit","gain","perte","richesse",
    "sante","medecin","hopital","maladie","soin","traitement","medicament","douleur","fievre","fatigue",
    "corps","esprit","cerveau","coeur","respiration","sommeil","energie","forme","repos","hygiene",
    "jeu","jouet","partie","niveau","score","defi","mission","aventure","quete","recompense",
    "victoire","defaite","egalite","strategie","chance","hasard","regle","objectif","progression","classement",
    "couleur","rouge","bleu","vert","jaune","noir","blanc","orange","violet","rose",
    "forme","cercle","carre","triangle","ligne","point","angle","surface","volume","espace",
    "faction","alliance","serveur","minecraft","bedrock","armure","epee","bouclier","ressource","territoire",
    "combat","recrue","officier","leader","victoire","forteresse","invasion","guilde","dragon","creeper",
    "zombie","squelette","diamant","emeraude","netherite","enchantement","potion","portail","zombie","squelette",
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
    word      = game["word"]
    guessed   = set(game["guessed"])
    errors    = game["errors"]
    display   = " ".join(l if l in guessed else "_" for l in word)
    wrong     = [l for l in guessed if l not in word]
    remaining = max(0, int(game.get("end_time", 0) - time.time()))
    mins, secs = divmod(remaining, 60)
    won  = all(l in guessed for l in word)
    lost = errors >= 6
    color = 0x2ECC71 if won else (0xE74C3C if lost else 0x9B59B6)
    embed = discord.Embed(title="🎯 Pendu — La Mystic", color=color)
    embed.add_field(name="Mot",         value=f"`{display}`",                                                    inline=False)
    embed.add_field(name="Dessin",      value=PENDU_ART[min(errors, 6)],                                         inline=False)
    embed.add_field(name="❌ Erreurs",  value=f"{errors}/6 — `{''.join(wrong) or 'aucune'}`",                    inline=True)
    embed.add_field(name="✅ Trouvées", value=f"`{''.join(sorted(l for l in guessed if l in word)) or 'aucune'}`", inline=True)
    embed.add_field(name="⏱️ Temps",    value=f"{mins}m {secs:02d}s",                                            inline=True)
    if game.get("participants"):
        embed.add_field(name="👥 Joueurs", value=", ".join(f"<@{u}>" for u in game["participants"]), inline=False)
    embed.set_footer(text="!devine [lettre]  •  !mot [mot complet]")
    return embed


async def _start_pendu_timer(channel_id: int, remaining: float):
    if channel_id in pendu_tasks:
        pendu_tasks[channel_id].cancel()
    async def _run():
        await asyncio.sleep(remaining)
        game = active_pendu.pop(channel_id, None)
        pendu_tasks.pop(channel_id, None)
        if not game:
            return
        save_games()
        channel = bot.get_channel(channel_id)
        if channel:
            await channel.send(f"⏰ Temps écoulé ! Le mot était : **{game['word']}**")
            if game.get("msg_id"):
                try:
                    m = await channel.fetch_message(game["msg_id"])
                    await m.delete()
                except Exception:
                    pass
    pendu_tasks[channel_id] = asyncio.create_task(_run())


class PenduView(discord.ui.View):
    def __init__(self, channel_id: int, creator_id: int):
        super().__init__(timeout=60)
        self.channel_id = channel_id
        self.creator_id = creator_id

    @discord.ui.button(label="🎲 Mot aléatoire", style=discord.ButtonStyle.green)
    async def random_word(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.creator_id:
            await interaction.response.send_message("❌ Seul le créateur peut choisir.", ephemeral=True)
            return
        word = random.choice(PENDU_MOTS)
        await self._launch(interaction, word)

    @discord.ui.button(label="✍️ Mot personnalisé", style=discord.ButtonStyle.blurple)
    async def custom_word(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.creator_id:
            await interaction.response.send_message("❌ Seul le créateur peut choisir.", ephemeral=True)
            return
        # Supprime le message de choix immédiatement
        await interaction.response.edit_message(content="📩 DM envoyé pour le mot !", view=None)
        try:
            dm = await interaction.user.create_dm()
            await dm.send("✍️ Entre le mot (lettres minuscules, sans accents) :")
            def chk(m):
                return m.author.id == interaction.user.id and isinstance(m.channel, discord.DMChannel)
            dm_msg = await bot.wait_for("message", check=chk, timeout=60)
            word   = dm_msg.content.strip().lower()
            if not word.isalpha():
                await dm.send("❌ Mot invalide.")
                return
            channel = bot.get_channel(self.channel_id)
            if channel and self.channel_id not in active_pendu:
                end_time = time.time() + 30 * 60
                game = {"word": word, "guessed": [], "errors": 0,
                        "creator": interaction.user.id, "participants": [],
                        "msg_id": None, "letter_cd": {}, "end_time": end_time}
                active_pendu[self.channel_id] = game
                msg = await channel.send(embed=build_pendu_embed(game))
                game["msg_id"] = msg.id
                save_games()
                await _start_pendu_timer(self.channel_id, 30 * 60)
                await dm.send(f"✅ Partie lancée avec le mot `{word}` !")
        except asyncio.TimeoutError:
            pass
        except Exception as e:
            print(f"[PENDU] Erreur DM : {e}")

    async def _launch(self, interaction: discord.Interaction, word: str):
        self.stop()
        end_time = time.time() + 30 * 60
        game = {"word": word, "guessed": [], "errors": 0,
                "creator": interaction.user.id, "participants": [],
                "msg_id": None, "letter_cd": {}, "end_time": end_time}
        active_pendu[self.channel_id] = game
        # Supprime le message de choix, envoie l'embed de jeu
        await interaction.response.edit_message(content=None, embed=build_pendu_embed(game), view=None)
        msg = await interaction.original_response()
        game["msg_id"] = msg.id
        save_games()
        await _start_pendu_timer(self.channel_id, 30 * 60)


async def _end_pendu(channel, game: dict, won: bool, winner_id: int = None):
    ch_id = channel.id
    active_pendu.pop(ch_id, None)
    if ch_id in pendu_tasks:
        pendu_tasks[ch_id].cancel()
        pendu_tasks.pop(ch_id, None)
    save_games()
    if game.get("msg_id"):
        try:
            msg = await channel.fetch_message(game["msg_id"])
            await msg.edit(embed=build_pendu_embed(game))
        except Exception:
            pass
    if won:
        data = load_user_data()
        if winner_id:
            u = get_user(data, winner_id)
            u["xp"] += 150
        save_user_data(data)
        winner_mention = f"<@{winner_id}>" if winner_id else "Quelqu'un"
        await channel.send(f"🏆 {winner_mention} a trouvé le mot **{game['word']}** ! **+150 XP** 🎉")
    else:
        await channel.send(f"💀 Perdu ! Le mot était **{game['word']}**.")
        mute_role = discord.utils.get(channel.guild.roles, name="Muted")
        if not mute_role:
            try:
                mute_role = await channel.guild.create_role(name="Muted")
                for ch in channel.guild.channels:
                    await ch.set_permissions(mute_role, send_messages=False, speak=False)
            except Exception:
                pass
        if mute_role:
            victims = [uid for uid in game["participants"] if uid != game["creator"]]
            for uid in victims:
                m = channel.guild.get_member(uid)
                if m:
                    try: await m.add_roles(mute_role, reason="Pendu perdu")
                    except Exception: pass
            await asyncio.sleep(30)
            for uid in victims:
                m = channel.guild.get_member(uid)
                if m and mute_role in m.roles:
                    try: await m.remove_roles(mute_role)
                    except Exception: pass


async def _update_pendu(ctx, game: dict, winner_id: int = None):
    guessed = set(game["guessed"])
    won     = all(l in guessed for l in game["word"])
    lost    = game["errors"] >= 6
    if game.get("msg_id"):
        try:
            msg = await ctx.channel.fetch_message(game["msg_id"])
            await msg.edit(embed=build_pendu_embed(game))
        except discord.NotFound:
            active_pendu.pop(ctx.channel.id, None)
            if ctx.channel.id in pendu_tasks:
                pendu_tasks[ctx.channel.id].cancel()
                pendu_tasks.pop(ctx.channel.id, None)
            save_games()
            return
        except Exception:
            pass
    if won:
        await _end_pendu(ctx.channel, game, won=True, winner_id=winner_id)
    elif lost:
        await _end_pendu(ctx.channel, game, won=False)


@bot.command(name="pendu")
async def pendu_cmd(ctx):
    if ctx.channel.id in active_pendu:
        await ctx.send("❌ Une partie est déjà en cours dans ce salon.", delete_after=5)
        return
    view = PenduView(ctx.channel.id, ctx.author.id)
    await ctx.send("🎯 **Pendu** — Comment veux-tu jouer ?", view=view)


@bot.command(name="devine")
async def devine_cmd(ctx, lettre: str = None):
    game = active_pendu.get(ctx.channel.id)
    if not game: await ctx.send("❌ Aucune partie en cours. Lance `!pendu`.", delete_after=5); return
    if ctx.author.id == game["creator"]: await ctx.send("❌ Le créateur ne peut pas jouer.", delete_after=5); return
    if lettre is None or len(lettre) != 1 or not lettre.isalpha():
        await ctx.send("❌ `!devine [lettre]`", delete_after=5); return
    lettre = lettre.lower()
    uid    = ctx.author.id
    now_m  = time.monotonic()
    if now_m - game["letter_cd"].get(str(uid), 0) < 3:
        await ctx.send("⏳ Attends 3 secondes.", delete_after=3); return
    game["letter_cd"][str(uid)] = now_m
    if lettre in game["guessed"]: await ctx.send(f"⚠️ `{lettre}` déjà jouée.", delete_after=4); return
    game["guessed"].append(lettre)
    if uid not in game["participants"]: game["participants"].append(uid)
    if lettre not in game["word"]: game["errors"] += 1
    save_games()
    try: await ctx.message.delete()
    except Exception: pass
    winner_id = uid if all(l in game["guessed"] for l in game["word"]) else None
    await _update_pendu(ctx, game, winner_id=winner_id)


@bot.command(name="mot")
async def mot_cmd(ctx, *, mot: str = None):
    game = active_pendu.get(ctx.channel.id)
    if not game: await ctx.send("❌ Aucune partie en cours.", delete_after=5); return
    if ctx.author.id == game["creator"]: await ctx.send("❌ Le créateur ne peut pas jouer.", delete_after=5); return
    if mot is None: await ctx.send("❌ `!mot [mot complet]`", delete_after=5); return
    mot = mot.lower().strip()
    uid = ctx.author.id
    if uid not in game["participants"]: game["participants"].append(uid)
    try: await ctx.message.delete()
    except Exception: pass
    if mot == game["word"]:
        for l in game["word"]:
            if l not in game["guessed"]: game["guessed"].append(l)
        save_games()
        await _update_pendu(ctx, game, winner_id=uid)
    else:
        game["errors"] += 1
        save_games()
        await _update_pendu(ctx, game)


@bot.command(name="pendustop")
async def pendustop_cmd(ctx):
    if not is_staff(ctx.author): await ctx.send("❌ Réservé aux Officiers et Leaders.", delete_after=5); return
    game = active_pendu.get(ctx.channel.id)
    if not game: await ctx.send("❌ Aucune partie en cours.", delete_after=5); return
    active_pendu.pop(ctx.channel.id, None)
    if ctx.channel.id in pendu_tasks:
        pendu_tasks[ctx.channel.id].cancel()
        pendu_tasks.pop(ctx.channel.id, None)
    save_games()
    await ctx.send(f"🛑 Partie arrêtée. Le mot était **{game['word']}**.")


# ═══════════════════════════════════════════════════════════════
#  MINI-JEU : MORPION
# ═══════════════════════════════════════════════════════════════
MORPION_EMOJIS = {None: "⬜", "X": "❌", "O": "⭕"}
WINS = [(0,1,2),(3,4,5),(6,7,8),(0,3,6),(1,4,7),(2,5,8),(0,4,8),(2,4,6)]


def check_winner(board: list) -> str | None:
    for a, b, c in WINS:
        if board[a] and board[a] == board[b] == board[c]:
            return board[a]
    return None


def build_morpion_embed(game: dict) -> discord.Embed:
    board     = game["board"]
    players   = game["players"]
    current   = game["current"]
    remaining = max(0, int(game.get("end_time", 0) - time.time()))
    mins, secs = divmod(remaining, 60)
    winner = check_winner(board)
    full   = all(c is not None for c in board)
    color  = 0x2ECC71 if winner else (0x95A5A6 if full else 0x3498DB)
    embed  = discord.Embed(title="❌⭕ Morpion — La Mystic", color=color)
    rows   = ""
    for i in range(0, 9, 3):
        rows += "".join(MORPION_EMOJIS[board[i+j]] for j in range(3)) + "\n"
    embed.add_field(name="Plateau", value=rows, inline=False)
    if winner:
        winner_id = players[0] if winner == "X" else players[1]
        embed.add_field(name="🏆 Gagnant", value=f"<@{winner_id}>", inline=True)
    elif full:
        embed.add_field(name="Résultat", value="🤝 Égalité !", inline=True)
    else:
        cur_id = players[current]
        sym    = "❌" if current == 0 else "⭕"
        embed.add_field(name="Tour",     value=f"{sym} <@{cur_id}>",   inline=True)
        embed.add_field(name="⏱️ Temps", value=f"{mins}m {secs:02d}s", inline=True)
    embed.add_field(name="Joueurs", value=f"❌ <@{players[0]}>  vs  ⭕ <@{players[1]}>", inline=False)
    return embed


class MorpionView(discord.ui.View):
    def __init__(self, channel_id: int):
        super().__init__(timeout=None)
        self.channel_id = channel_id
        self._rebuild()

    def _rebuild(self):
        self.clear_items()
        game  = active_morpion.get(self.channel_id)
        board = game["board"] if game else [None]*9
        ended = game is None or check_winner(board) is not None or all(c is not None for c in board)
        for i in range(9):
            row = i // 3
            lbl = MORPION_EMOJIS[board[i]]
            btn = discord.ui.Button(
                label=lbl,
                style=discord.ButtonStyle.secondary if board[i] is None else discord.ButtonStyle.primary,
                disabled=(board[i] is not None or ended),
                row=row,
                custom_id=f"morpion_{self.channel_id}_{i}"
            )
            btn.callback = self._make_callback(i)
            self.add_item(btn)

    def _make_callback(self, cell: int):
        async def callback(interaction: discord.Interaction):
            game = active_morpion.get(self.channel_id)
            if not game:
                await interaction.response.send_message("❌ Partie terminée.", ephemeral=True)
                return
            uid     = interaction.user.id
            current = game["current"]
            players = game["players"]
            if uid != players[current]:
                await interaction.response.send_message("❌ Ce n'est pas ton tour.", ephemeral=True)
                return
            if game["board"][cell] is not None:
                await interaction.response.send_message("❌ Case déjà jouée.", ephemeral=True)
                return
            sym = "X" if current == 0 else "O"
            game["board"][cell] = sym
            game["current"] = 1 - current
            save_games()
            winner = check_winner(game["board"])
            full   = all(c is not None for c in game["board"])
            if winner or full:
                active_morpion.pop(self.channel_id, None)
                if self.channel_id in morpion_tasks:
                    morpion_tasks[self.channel_id].cancel()
                    morpion_tasks.pop(self.channel_id, None)
                save_games()
                # Désactive tous les boutons
                for item in self.children:
                    item.disabled = True
                embed = build_morpion_embed(game)
                if winner:
                    winner_id = players[0] if winner == "X" else players[1]
                    loser_id  = players[1] if winner == "X" else players[0]
                    data = load_user_data()
                    u    = get_user(data, winner_id)
                    u["xp"] += 50
                    save_user_data(data)
                    revanche_view = RevancheView(loser_id, players, timeout_sec=10)
                    await interaction.response.edit_message(embed=embed, view=revanche_view)
                    await interaction.followup.send(f"🎉 <@{winner_id}> a gagné ! **+50 XP** 🏆")
                else:
                    await interaction.response.edit_message(embed=embed, view=None)
                    await interaction.followup.send("🤝 Égalité !")
            else:
                self._rebuild()
                await interaction.response.edit_message(embed=build_morpion_embed(game), view=self)
        return callback


class RevancheView(discord.ui.View):
    def __init__(self, loser_id: int, players: list, timeout_sec: int = 10):
        super().__init__(timeout=timeout_sec)
        self.loser_id = loser_id
        self.players  = players

    async def on_timeout(self):
        # Désactive le bouton revanche automatiquement
        for item in self.children:
            item.disabled = True

    @discord.ui.button(label="🔁 Revanche", style=discord.ButtonStyle.green)
    async def revanche(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.loser_id:
            await interaction.response.send_message("❌ Seul le perdant peut demander la revanche.", ephemeral=True)
            return
        self.stop()
        new_players = list(reversed(self.players))
        end_time    = time.time() + 5 * 60
        game = {"board": [None]*9, "players": new_players, "current": 0,
                "msg_id": None, "end_time": end_time}
        ch_id = interaction.channel.id
        active_morpion[ch_id] = game
        view  = MorpionView(ch_id)
        embed = build_morpion_embed(game)
        await interaction.response.send_message(embed=embed, view=view)
        msg = await interaction.original_response()
        game["msg_id"] = msg.id
        save_games()
        await _start_morpion_timer(ch_id, 5 * 60)


async def _start_morpion_timer(channel_id: int, remaining: float):
    if channel_id in morpion_tasks:
        morpion_tasks[channel_id].cancel()
    async def _run():
        await asyncio.sleep(remaining)
        game = active_morpion.pop(channel_id, None)
        morpion_tasks.pop(channel_id, None)
        if not game:
            return
        save_games()
        channel = bot.get_channel(channel_id)
        if channel:
            await channel.send("⏰ Temps écoulé ! Partie de morpion annulée.")
            if game.get("msg_id"):
                try:
                    m = await channel.fetch_message(game["msg_id"])
                    await m.edit(view=None)
                except Exception:
                    pass
    morpion_tasks[channel_id] = asyncio.create_task(_run())


@bot.command(name="morpion")
async def morpion_cmd(ctx, opponent: discord.Member = None):
    if opponent is None: await ctx.send("❌ `!morpion @joueur`", delete_after=5); return
    if opponent.bot or opponent.id == ctx.author.id: await ctx.send("❌ Adversaire invalide.", delete_after=5); return
    if ctx.channel.id in active_morpion: await ctx.send("❌ Partie déjà en cours.", delete_after=5); return
    for g in active_morpion.values():
        if ctx.author.id in g["players"] or opponent.id in g["players"]:
            await ctx.send("❌ Un joueur est déjà dans une partie.", delete_after=5); return
    end_time = time.time() + 5 * 60
    game = {"board": [None]*9, "players": [ctx.author.id, opponent.id],
            "current": 0, "msg_id": None, "end_time": end_time}
    active_morpion[ctx.channel.id] = game
    view  = MorpionView(ctx.channel.id)
    embed = build_morpion_embed(game)
    msg   = await ctx.send(embed=embed, view=view)
    game["msg_id"] = msg.id
    save_games()
    await _start_morpion_timer(ctx.channel.id, 5 * 60)


@bot.command(name="morpionstop")
async def morpionstop_cmd(ctx):
    if not is_staff(ctx.author): await ctx.send("❌ Réservé aux Officiers et Leaders.", delete_after=5); return
    game = active_morpion.get(ctx.channel.id)
    if not game: await ctx.send("❌ Aucune partie en cours.", delete_after=5); return
    active_morpion.pop(ctx.channel.id, None)
    if ctx.channel.id in morpion_tasks:
        morpion_tasks[ctx.channel.id].cancel()
        morpion_tasks.pop(ctx.channel.id, None)
    save_games()
    await ctx.send("🛑 Partie de morpion arrêtée par un admin.")


# ═══════════════════════════════════════════════════════════════
#  GIVEAWAY
# ═══════════════════════════════════════════════════════════════
import re as _re


def build_giveaway_embed(gw: dict) -> discord.Embed:
    ends  = discord.utils.format_dt(datetime.fromtimestamp(gw["ends_at"], tz=timezone.utc), style="R")
    embed = discord.Embed(title=f"🎉 GIVEAWAY — {gw['reward']}",
        description="Clique sur **🎉 Participer** pour tenter ta chance !", color=0xF1C40F)
    embed.add_field(name="⏰ Fin",          value=ends,                         inline=True)
    embed.add_field(name="👥 Participants", value=str(len(gw["participants"])), inline=True)
    embed.add_field(name="🏆 Récompense",  value=gw["reward"],                 inline=False)
    embed.set_footer(text=f"Organisé par {gw['host']}")
    return embed


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
        try:
            msg = await interaction.channel.fetch_message(self.msg_id)
            await msg.edit(embed=build_giveaway_embed(gw))
        except Exception:
            pass


def parse_duration(s: str) -> int | None:
    total = 0
    for val, unit in _re.findall(r"(\d+)([smhj])", s.lower()):
        v = int(val)
        if unit == "s":   total += v
        elif unit == "m": total += v * 60
        elif unit == "h": total += v * 3600
        elif unit == "j": total += v * 86400
    return total if total > 0 else None


@bot.command(name="giveaway", aliases=["gw"])
async def giveaway_cmd(ctx, duree: str = None, *, reward: str = None):
    if not any(r.id in GIVEAWAY_ROLE_IDS for r in ctx.author.roles) and not ctx.author.guild_permissions.administrator:
        await ctx.send("❌ Réservé aux Officiers et Leaders.", delete_after=5); return
    if duree is None or reward is None:
        await ctx.send("❌ `!giveaway 1h Rang VIP`", delete_after=8); return
    seconds = parse_duration(duree)
    if not seconds:
        await ctx.send("❌ Durée invalide. Ex : `10m`, `1h`, `2h30m`", delete_after=8); return
    ends_at = time.time() + seconds
    gw = {"reward": reward, "ends_at": ends_at, "participants": [],
          "host": str(ctx.author), "channel_id": ctx.channel.id}
    embed = build_giveaway_embed(gw)
    msg   = await ctx.send(embed=embed, view=GiveawayView(0))
    gw_id = msg.id
    active_giveaways[gw_id] = gw
    await msg.edit(view=GiveawayView(gw_id))
    asyncio.create_task(_end_giveaway(gw_id, seconds, ctx.channel, reward))


async def _end_giveaway(gw_id: int, delay: int, channel: discord.TextChannel, reward: str):
    await asyncio.sleep(delay)
    gw = active_giveaways.pop(gw_id, None)
    if not gw:
        return
    try:
        msg = await channel.fetch_message(gw_id)
        if not gw["participants"]:
            embed = discord.Embed(title=f"🎉 GIVEAWAY TERMINÉ — {reward}",
                description="😔 Aucun participant...", color=0x95A5A6)
            await msg.edit(embed=embed, view=None)
            return
        winner_id = random.choice(gw["participants"])
        winner    = channel.guild.get_member(winner_id)
        name      = winner.mention if winner else f"<@{winner_id}>"
        embed = discord.Embed(title=f"🎉 GIVEAWAY TERMINÉ — {reward}",
            description=f"🏆 Gagnant : {name}\n🎊 Félicitations !", color=0x2ECC71)
        embed.set_footer(text=f"Organisé par {gw['host']} • {len(gw['participants'])} participants")
        await msg.edit(embed=embed, view=None)
        await channel.send(f"🎊 Félicitations {name} ! Tu as gagné **{reward}** !")
    except Exception as e:
        print(f"[GW] Erreur fin giveaway : {e}")


# ═══════════════════════════════════════════════════════════════
#  CLASSEMENT
# ═══════════════════════════════════════════════════════════════
@bot.command(name="classement", aliases=["top", "leaderboard"])
async def classement_cmd(ctx):
    data  = load_user_data()
    guild = ctx.guild
    now   = time.time()
    medals = ["🥇", "🥈", "🥉"]

    for uid_str, u in data.items():
        u["_voice_live"] = u["voice_time"] + (now - u["voice_join"]) if u.get("voice_join") else u["voice_time"]

    def top10_field(key: str, fmt) -> str:
        items = sorted([(uid, u) for uid, u in data.items() if u.get(key, 0) > 0],
                        key=lambda x: x[1].get(key, 0), reverse=True)[:10]
        if not items: return "_Aucun joueur_"
        lines = []
        for i, (uid, u) in enumerate(items):
            m    = guild.get_member(int(uid))
            name = m.display_name if m else "Inconnu"
            rank = medals[i] if i < 3 else f"`#{i+1}`"
            lines.append(f"{rank} **{name}** — {fmt(u)}")
        return "\n".join(lines)

    items_lvl = sorted(data.items(), key=lambda x: (x[1].get("level", 0), x[1].get("xp", 0)), reverse=True)[:10]
    top_lvl = "\n".join(
        f"{medals[i] if i < 3 else f'`#{i+1}`'} **{guild.get_member(int(uid)).display_name if guild.get_member(int(uid)) else 'Inconnu'}** — Niv. {u.get('level',0)} ({u.get('xp',0)} XP)"
        for i, (uid, u) in enumerate(items_lvl)
    ) or "_Aucun joueur_"

    faction_members = [
        (uid, u, guild.get_member(int(uid)))
        for uid, u in data.items()
        if guild.get_member(int(uid)) and any(r.id in FACTION_ROLE_IDS for r in guild.get_member(int(uid)).roles)
    ]
    faction_members.sort(key=lambda x: (x[1].get("level", 0), x[1].get("xp", 0)), reverse=True)
    top_faction = "\n".join(
        f"{medals[i] if i < 3 else f'`#{i+1}`'} **{m.display_name}** — Niv. {u.get('level',0)}"
        for i, (uid, u, m) in enumerate(faction_members[:10])
    ) or "_Aucun membre faction_"

    embed = discord.Embed(title="🏆 Classements — La Mystic", color=0xF1C40F, timestamp=now_utc())
    embed.add_field(name="━━━━━━━━━━━━━━━━━━\n📊 Top Messages", value=top10_field("message_count", lambda u: f"{u['message_count']} msg"), inline=False)
    embed.add_field(name="━━━━━━━━━━━━━━━━━━\n⭐ Top Niveau",   value=top_lvl,  inline=False)
    embed.add_field(name="━━━━━━━━━━━━━━━━━━\n🎤 Top Vocal",    value=top10_field("_voice_live", lambda u: fmt_voice(u["_voice_live"])), inline=False)
    embed.add_field(name="━━━━━━━━━━━━━━━━━━\n⚔️ Top Faction",  value=top_faction, inline=False)
    embed.set_footer(text="Top 10 par catégorie • Temps vocal live inclus")
    await ctx.send(embed=embed)


# ═══════════════════════════════════════════════════════════════
#  RESTORE AU DÉMARRAGE
# ═══════════════════════════════════════════════════════════════
async def _restore_games():
    raw = load_games()
    now = time.time()
    restored = 0
    for key, data in raw.items():
        if key.startswith("pendu_"):
            ch_id     = int(key.split("_", 1)[1])
            remaining = data.get("end_time", 0) - now
            if remaining <= 0:
                continue
            data["guessed"]   = list(data.get("guessed", []))
            data["letter_cd"] = {}
            active_pendu[ch_id] = data
            await _start_pendu_timer(ch_id, remaining)
            restored += 1
            print(f"[RESTORE] Pendu restauré : ch={ch_id} ({int(remaining)}s)")
        elif key.startswith("morpion_"):
            ch_id     = int(key.split("_", 1)[1])
            remaining = data.get("end_time", 0) - now
            if remaining <= 0:
                continue
            active_morpion[ch_id] = data
            await _start_morpion_timer(ch_id, remaining)
            restored += 1
            print(f"[RESTORE] Morpion restauré : ch={ch_id} ({int(remaining)}s)")
    if restored:
        print(f"[RESTORE] {restored} partie(s) restaurée(s)")


# ═══════════════════════════════════════════════════════════════
#  COMMANDE AIDE
# ═══════════════════════════════════════════════════════════════
bot.remove_command("help")


@bot.command(name="help", aliases=["aide", "commandes"])
async def help_cmd(ctx):
    staff = is_staff(ctx.author)
    embed = discord.Embed(title="📖 Aide — Commandes du bot",
        description="Voici toutes les commandes disponibles.\n*(🔒 = réservé au staff)*", color=0x9B59B6)
    embed.add_field(name="━━━━━━━━━━━━━━━━━━\n👤 Commandes générales",
        value="`!info @membre` — Infos d'un membre\n`!level` — Ton niveau XP\n`!classement` — Top 10\n`!help` — Ce message", inline=False)
    embed.add_field(name="━━━━━━━━━━━━━━━━━━\n🎫 Tickets",
        value="`!ticket` 🔒 — Panneau tickets\n`!fermer` — Ferme le ticket", inline=False)
    embed.add_field(name="━━━━━━━━━━━━━━━━━━\n📋 Roster",
        value="`!roster` 🔒 — Met à jour le roster", inline=False)
    if staff:
        embed.add_field(name="━━━━━━━━━━━━━━━━━━\n🔨 Modération 🔒",
            value=("`!ban @membre [raison]` — Bannit\n`!kick @membre [raison]` — Expulse\n"
                   "`!mute @membre [raison]` — Mute\n`!unmute @membre` — Unmute\n"
                   "`!effacer <n>` — Supprime n messages\n`!pub` — Envoie la pub de recrutement"), inline=False)
    embed.add_field(name="━━━━━━━━━━━━━━━━━━\n🎯 Mini-jeux",
        value=("**Pendu**\n`!pendu` — Lance une partie\n`!devine [lettre]` — Deviner une lettre\n"
               "`!mot [mot]` — Deviner le mot\n`!pendustop` 🔒 — Arrête la partie\n\n"
               "**Morpion**\n`!morpion @joueur` — Lancer un 1v1\n`!morpionstop` 🔒 — Arrête la partie\n\n"
               "**Autres**\n`!pileouface` — Pile ou face\n`!giveaway [durée] [récompense]` 🔒 — Giveaway"), inline=False)
    embed.add_field(name="━━━━━━━━━━━━━━━━━━\n🛡️ Protections automatiques",
        value=("🔗 **Anti-liens** — Liens supprimés automatiquement\n"
               "⚡ **Anti-spam** — +4 msgs en 6s = avertissement puis expulsion"), inline=False)
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
    print(f"   WELCOME_CHANNEL_ID = {WELCOME_CHANNEL_ID}")
    print(f"   Anti-spam         : {SPAM_LIMIT} msgs / {SPAM_WINDOW}s")
    # Réenregistre les vues persistantes pour les tickets
    bot.add_view(TicketView())
    await _restore_games()


@bot.event
async def on_command_error(ctx: commands.Context, error):
    if isinstance(error, commands.CommandNotFound):
        await ctx.send(
            "❌ La commande que tu as entrée n'existe pas. "
            "Essayez `!help` ou `!commandes` pour voir la liste des commandes disponibles.",
            delete_after=8
        )
    elif isinstance(error, commands.CheckFailure):
        pass
    else:
        print(f"[ERROR] {ctx.command} : {error}")


TOKEN = os.environ.get("DISCORD_TOKEN")
bot.run(TOKEN)
