import discord
from discord.ext import commands
import asyncio

intents = discord.Intents.all()
bot = commands.Bot(command_prefix="!", intents=intents)

ROLE_ID = 913064374590140417
CATEGORY_ID = 1419109736091095090
ROLE_AUTORISE = 703339900929441803

@bot.event
async def on_ready():
    print(f"✅ Mystic Bot connecté : {bot.user}")

class TicketView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="📋 Demande de recrutement", style=discord.ButtonStyle.green)
    async def recrutement(self, interaction: discord.Interaction, button: discord.ui.Button):
        await creer_ticket(interaction, "recrutement")

    @discord.ui.button(label="📩 Autre demande", style=discord.ButtonStyle.blurple)
    async def autre(self, interaction: discord.Interaction, button: discord.ui.Button):
        await creer_ticket(interaction, "autre")

async def creer_ticket(interaction, type):
    guild = interaction.guild
    role = guild.get_role(ROLE_ID)
    category = guild.get_channel(CATEGORY_ID)

    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        interaction.user: discord.PermissionOverwrite(view_channel=True, send_messages=True),
        role: discord.PermissionOverwrite(view_channel=True, send_messages=True),
    }

    channel = await guild.create_text_channel(
        f"ticket-{interaction.user.name}",
        category=category,
        overwrites=overwrites
    )

    if type == "recrutement":
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
            f"Pour fermer le ticket tape `!fermer`"
        )

    await channel.send(texte)
    await interaction.response.send_message(
        f"✅ Ton ticket a été créé : {channel.mention}",
        ephemeral=True
    )

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
    if "ticket-" in ctx.channel.name:
        await ctx.send("🔒 Fermeture du ticket dans 5 secondes...")
        await asyncio.sleep(5)
        await ctx.channel.delete()

import os
bot.run(os.environ["TOKEN"])