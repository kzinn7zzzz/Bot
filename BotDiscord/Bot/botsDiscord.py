import os
import re
import unicodedata

import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv


load_dotenv()


TOKEN = os.getenv("DISCORD_TOKEN")
GUILD_ID = os.getenv("DISCORD_GUILD_ID")
TICKET_CATEGORY_NAME = os.getenv("TICKET_CATEGORY", "Tickets")
SUPPORT_ROLE_NAME = os.getenv("SUPPORT_ROLE", "Suporte")
TICKET_BANNER_URL = os.getenv("TICKET_BANNER_URL")
TICKET_COLOR = os.getenv("TICKET_COLOR", "00DEE8")
TICKET_TYPES = {
	"Bugs": ("🐛", "Encontrou algum problema? Reporte aqui."),
	"Vendas": ("💰", "Dúvidas sobre compras, produtos ou pagamentos."),
	"Suporte": ("🛠️", "Precisa de ajuda? Abra seu atendimento aqui."),
	"Parceria": ("🤝", "Fale com a equipe sobre uma parceria."),
}


def get_brand_color() -> discord.Color:
	try:
		return discord.Color(int(TICKET_COLOR.lstrip("#"), 16))
	except ValueError:
		return discord.Color.from_rgb(0, 222, 232)


BRAND_COLOR = get_brand_color()


def get_guild_id() -> int | None:
	if GUILD_ID and GUILD_ID.isdigit():
		return int(GUILD_ID)
	return None


def get_ticket_channel_name(ticket_type: str, username: str) -> str:
	emoji, _ = TICKET_TYPES[ticket_type]
	clean_username = unicodedata.normalize("NFKD", username)
	clean_username = "".join(
		character for character in clean_username
		if not unicodedata.combining(character)
	)
	clean_username = re.sub(r"[^a-zA-Z0-9-]+", "-", clean_username).strip("-").lower()
	clean_username = clean_username or "usuario"
	return f"{emoji}・{ticket_type.lower()}・{clean_username}"[:100]


class TicketBot(commands.Bot):
	def __init__(self) -> None:
		intents = discord.Intents.default()
		super().__init__(command_prefix="!", intents=intents)

	async def setup_hook(self) -> None:
		self.add_view(TicketPanelView())
		self.add_view(TicketChannelView())
		self.add_view(BoxedStaffView())

		guild_id = get_guild_id()
		if guild_id:
			guild = discord.Object(id=guild_id)
			self.tree.copy_global_to(guild=guild)
			await self.tree.sync(guild=guild)
		else:
			await self.tree.sync()

	async def on_ready(self) -> None:
		for guild in self.guilds:
			await move_bot_role_to_top(guild)
		print(f"Bot conectado como {self.user} (ID: {self.user.id})")


bot = TicketBot()


async def move_bot_role_to_top(guild: discord.Guild) -> None:
	member = guild.me
	if not member or not member.guild_permissions.manage_roles:
		return
	bot_role = member.top_role
	if bot_role.is_default() or bot_role.managed:
		return
	try:
		await guild.edit_role_positions(
			positions={bot_role: len(guild.roles) - 1},
			reason="Posicionando o cargo do bot acima dos demais cargos gerenciáveis",
		)
	except discord.Forbidden:
		print(
			f"Não foi possível mover o cargo do bot em {guild.name}. "
			"O dono do servidor precisa posicioná-lo acima dos cargos de suporte."
		)


def is_support_or_admin(member: discord.Member) -> bool:
	return member.guild_permissions.administrator or any(
		role.name == SUPPORT_ROLE_NAME for role in member.roles
	)


async def get_ticket_category(
	guild: discord.Guild, ticket_type: str
) -> discord.CategoryChannel:
	category = discord.utils.get(guild.categories, name=ticket_type)
	if category:
		return category
	return await guild.create_category(
		ticket_type,
		reason="Categoria criada pelo sistema de tickets",
	)


class TicketPanelView(discord.ui.View):
	def __init__(self) -> None:
		super().__init__(timeout=None)
		self.add_item(TicketTypeSelect())


class TicketTypeSelect(discord.ui.Select):
	def __init__(self) -> None:
		options = [
			discord.SelectOption(
				label=ticket_type,
				value=ticket_type,
				description=description,
				emoji=emoji,
			)
			for ticket_type, (emoji, description) in TICKET_TYPES.items()
		]
		super().__init__(
			placeholder="Clique aqui para selecionar...",
			options=options,
			custom_id="tickets:type",
		)

	async def callback(self, interaction: discord.Interaction) -> None:
		await interaction.response.send_modal(TicketModal(self.values[0]))


class TicketModal(discord.ui.Modal, title="Abrir ticket"):
	def __init__(self, ticket_type: str) -> None:
		super().__init__()
		self.ticket_type = ticket_type

	assunto = discord.ui.TextInput(
		label="Assunto",
		placeholder="Ex.: Preciso de ajuda com uma compra",
		max_length=100,
	)
	detalhes = discord.ui.TextInput(
		label="Como podemos ajudar?",
		placeholder="Descreva sua solicitação com o máximo de detalhes.",
		style=discord.TextStyle.paragraph,
		max_length=1000,
	)

	async def on_submit(self, interaction: discord.Interaction) -> None:
		if not interaction.guild or not isinstance(interaction.user, discord.Member):
			await interaction.response.send_message(
				"Este comando só pode ser usado dentro de um servidor.", ephemeral=True
			)
			return

		existing_ticket = discord.utils.find(
			lambda channel: isinstance(channel, discord.TextChannel)
			and channel.topic == f"ticket:{interaction.user.id}",
			interaction.guild.text_channels,
		)
		if existing_ticket:
			await interaction.response.send_message(
				f"Você já possui um ticket aberto: {existing_ticket.mention}",
				ephemeral=True,
			)
			return

		category = await get_ticket_category(interaction.guild, self.ticket_type)
		support_role = discord.utils.get(
			interaction.guild.roles, name=SUPPORT_ROLE_NAME
		)
		overwrites = {
			interaction.guild.default_role: discord.PermissionOverwrite(view_channel=False),
			interaction.user: discord.PermissionOverwrite(
				view_channel=True,
				send_messages=True,
				read_message_history=True,
				attach_files=True,
			),
			interaction.guild.me: discord.PermissionOverwrite(
				view_channel=True,
				send_messages=True,
				read_message_history=True,
				manage_channels=True,
			),
		}
		if support_role:
			overwrites[support_role] = discord.PermissionOverwrite(
				view_channel=True,
				send_messages=True,
				read_message_history=True,
			)

		channel = await interaction.guild.create_text_channel(
			name=get_ticket_channel_name(self.ticket_type, interaction.user.name),
			category=category,
			topic=f"ticket:{interaction.user.id}",
			overwrites=overwrites,
			reason="Novo ticket aberto",
		)
		emoji, _ = TICKET_TYPES[self.ticket_type]
		embed = discord.Embed(
			title=f"{emoji} Atendimento {self.ticket_type}",
			description=(
				"Obrigado por entrar em contato com a nossa equipe.\n"
				"Explique sua situação com detalhes e aguarde o atendimento."
			),
			color=BRAND_COLOR,
		)
		embed.add_field(name="Assunto", value=self.assunto.value, inline=False)
		embed.add_field(name="Descrição", value=self.detalhes.value, inline=False)
		embed.add_field(name="Solicitante", value=interaction.user.mention, inline=True)
		embed.add_field(name="Status", value="🟢 Aguardando atendimento", inline=True)
		embed.add_field(
			name="OBS",
			value=(
				"Mantenha sua DM aberta para receber uma cópia deste ticket "
				"e a opção de avaliar seu atendimento."
			),
			inline=False,
		)
		if interaction.guild.icon:
			embed.set_author(
				name=f"Atendimento {interaction.guild.name}",
				icon_url=interaction.guild.icon.url,
			)
		embed.set_footer(text="Use o botão abaixo para encerrar este atendimento")
		if TICKET_BANNER_URL:
			embed.set_image(url=TICKET_BANNER_URL)
		await channel.send(
			content=(
				f"{interaction.user.mention} **seu atendimento foi criado!**\n"
				"A equipe já foi avisada e responderá assim que possível."
			),
			embed=embed,
		)
		await channel.send(view=BoxedStaffView())
		await interaction.response.send_message(
			f"Ticket criado com sucesso: {channel.mention}", ephemeral=True
		)


class TicketChannelView(discord.ui.View):
	def __init__(self) -> None:
		super().__init__(timeout=None)

	@discord.ui.button(
		label="Fechar ticket",
		style=discord.ButtonStyle.danger,
		emoji="🔒",
		custom_id="tickets:close",
	)
	async def close_ticket(
		self, interaction: discord.Interaction, button: discord.ui.Button
	) -> None:
		if not interaction.guild or not isinstance(interaction.user, discord.Member):
			return
		channel = interaction.channel
		if not isinstance(channel, discord.TextChannel) or not channel.topic.startswith(
			"ticket:"
		):
			await interaction.response.send_message(
				"Este botão só pode ser usado em um canal de ticket.", ephemeral=True
			)
			return

		owner_id = get_ticket_owner_id(channel)
		if owner_id is None:
			await interaction.response.send_message("Não foi possível identificar o autor do ticket.", ephemeral=True)
			return
		if interaction.user.id != owner_id and not is_support_or_admin(interaction.user):
			await interaction.response.send_message(
				"Apenas o autor do ticket ou a equipe de suporte pode fechá-lo.",
				ephemeral=True,
			)
			return

		await delete_ticket_call(channel)
		await interaction.response.send_message("Ticket será fechado em instantes.")
		await channel.delete(reason=f"Ticket fechado por {interaction.user}")

	@discord.ui.button(
		label="Como libero minha DM?",
		style=discord.ButtonStyle.secondary,
		emoji="❓",
		custom_id="tickets:help-dm",
	)
	async def dm_help(
		self, interaction: discord.Interaction, button: discord.ui.Button
	) -> None:
		await interaction.response.send_message(
			"Abra seu perfil do Discord, acesse **Privacidade e segurança** "
			"e permita mensagens diretas de membros do servidor.",
			ephemeral=True,
		)


def is_ticket_channel(channel: discord.abc.GuildChannel) -> bool:
	return isinstance(channel, discord.TextChannel) and bool(
		channel.topic and channel.topic.startswith("ticket:")
	)


async def require_staff(interaction: discord.Interaction) -> bool:
	if not isinstance(interaction.user, discord.Member) or not is_support_or_admin(
		interaction.user
	):
		await interaction.response.send_message(
			"Esta ação é exclusiva da equipe de suporte.", ephemeral=True
		)
		return False
	if not is_ticket_channel(interaction.channel):
		await interaction.response.send_message(
			"Esta ação só pode ser usada dentro de um ticket.", ephemeral=True
		)
		return False
	return True


def get_member_id(value: str) -> int | None:
	clean_value = value.strip()
	if clean_value.isdigit() and 15 <= len(clean_value) <= 20:
		return int(clean_value)
	match = re.fullmatch(r"<@!?([0-9]{15,20})>", clean_value)
	return int(match.group(1)) if match else None


def get_ticket_owner_id(channel: discord.TextChannel) -> int | None:
	parts = (channel.topic or "").split(":")
	if len(parts) >= 2 and parts[0] == "ticket" and parts[1].isdigit():
		return int(parts[1])
	return None


def get_ticket_call_id(channel: discord.TextChannel) -> int | None:
	parts = (channel.topic or "").split(":")
	if len(parts) >= 4 and parts[0] == "ticket" and parts[2] == "call":
		return int(parts[3]) if parts[3].isdigit() else None
	return None


async def delete_ticket_call(channel: discord.TextChannel) -> None:
	call_id = get_ticket_call_id(channel)
	if not call_id or not channel.guild:
		return
	call = channel.guild.get_channel(call_id)
	if isinstance(call, discord.VoiceChannel):
		try:
			await call.delete(reason="Call removida junto com o ticket")
		except discord.NotFound:
			pass


class TicketStaffView(discord.ui.View):
	def __init__(self) -> None:
		super().__init__(timeout=None)

	@discord.ui.button(label="Avisar Autor", emoji="🔔", style=discord.ButtonStyle.secondary, row=0, custom_id="tickets:call-member")
	async def call_member(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
		if not await require_staff(interaction):
			return
		channel = interaction.channel
		if isinstance(channel, discord.TextChannel):
			owner_id = get_ticket_owner_id(channel)
			if owner_id is None:
				await interaction.response.send_message("Não foi possível identificar o autor do ticket.", ephemeral=True)
				return
			await interaction.response.send_message(
					f"🔔 <@{owner_id}> um responsável já irá atender você!"
			)

	@discord.ui.button(label="Convidar Pessoa", emoji="➕", style=discord.ButtonStyle.secondary, row=0, custom_id="tickets:add-member")
	async def add_member(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
		if await require_staff(interaction):
			await interaction.response.send_modal(MemberAccessModal("Adicionar membro", True))

	@discord.ui.button(label="Retirar Pessoa", emoji="❌", style=discord.ButtonStyle.secondary, row=0, custom_id="tickets:remove-member")
	async def remove_member(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
		if await require_staff(interaction):
			await interaction.response.send_modal(MemberAccessModal("Remover membro", False))

	@discord.ui.button(label="Mover Atendimento", emoji="🔄", style=discord.ButtonStyle.secondary, row=1, custom_id="tickets:move")
	async def move(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
		if await require_staff(interaction):
			await interaction.response.send_modal(MoveTicketModal())

	@discord.ui.button(label="Editar Canal", emoji="📝", style=discord.ButtonStyle.secondary, row=2, custom_id="tickets:rename")
	async def rename(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
		if await require_staff(interaction):
			await interaction.response.send_modal(RenameTicketModal())

	@discord.ui.button(label="Nota Privada", emoji="📋", style=discord.ButtonStyle.secondary, row=2, custom_id="tickets:note")
	async def note(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
		if await require_staff(interaction):
			await interaction.response.send_modal(NoteModal())

	@discord.ui.button(label="Abrir Sala de Voz", emoji="🎙️", style=discord.ButtonStyle.secondary, row=3, custom_id="tickets:create-call")
	async def create_call(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
		if not await require_staff(interaction):
			return
		channel = interaction.channel
		if not isinstance(channel, discord.TextChannel) or not interaction.guild:
			return
		if get_ticket_call_id(channel):
			await interaction.response.send_message(
				"Este ticket já possui uma sala de voz associada.", ephemeral=True
			)
			return
		overwrites = dict(channel.overwrites)
		voice = await interaction.guild.create_voice_channel(
			name=f"call-{channel.name}"[:100],
			category=channel.category,
			overwrites=overwrites,
			reason=f"Call criada por {interaction.user}",
		)
		owner_id = get_ticket_owner_id(channel)
		if owner_id is not None:
			await channel.edit(topic=f"ticket:{owner_id}:call:{voice.id}")
		await interaction.response.send_message(f"Call criada: {voice.mention}", ephemeral=True)

	@discord.ui.button(label="Assumir Caso", emoji="🫡", style=discord.ButtonStyle.secondary, row=3, custom_id="tickets:assume")
	async def assume(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
		if await require_staff(interaction):
			await interaction.response.send_message(
				f"🫡 Atendimento assumido por {interaction.user.mention}.",
			)

	@discord.ui.button(label="Concluir Atendimento", emoji="✅", style=discord.ButtonStyle.success, row=4, custom_id="tickets:finish")
	async def finish(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
		if not await require_staff(interaction):
			return
		channel = interaction.channel
		if isinstance(channel, discord.TextChannel):
			await delete_ticket_call(channel)
			await interaction.response.send_message("✅ Ticket finalizado. O canal será removido.")
			await channel.delete(reason=f"Ticket finalizado por {interaction.user}")

	@discord.ui.button(label="Repassar Caso", emoji="🔄", style=discord.ButtonStyle.primary, row=4, custom_id="tickets:transfer")
	async def transfer(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
		if await require_staff(interaction):
			await interaction.response.send_modal(TransferModal())

	@discord.ui.button(label="Excluir Atendimento", emoji="🔒", style=discord.ButtonStyle.danger, row=4, custom_id="tickets:staff-close")
	async def close_ticket(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
		channel = interaction.channel
		if not isinstance(channel, discord.TextChannel) or not channel.topic.startswith("ticket:"):
			await interaction.response.send_message("Este botão só pode ser usado dentro de um ticket.", ephemeral=True)
			return
		owner_id = get_ticket_owner_id(channel)
		if owner_id is None:
			await interaction.response.send_message("Não foi possível identificar o autor do ticket.", ephemeral=True)
			return
		if interaction.user.id != owner_id and not isinstance(interaction.user, discord.Member):
			await interaction.response.send_message("Você não pode fechar este ticket.", ephemeral=True)
			return
		if isinstance(interaction.user, discord.Member) and interaction.user.id != owner_id and not is_support_or_admin(interaction.user):
			await interaction.response.send_message("Você não pode fechar este ticket.", ephemeral=True)
			return
		await delete_ticket_call(channel)
		await interaction.response.send_message("Ticket será fechado em instantes.")
		await channel.delete(reason=f"Ticket fechado por {interaction.user}")

class BoxedStaffView(discord.ui.LayoutView):
	def __init__(self) -> None:
		super().__init__(timeout=None)
		legacy_view = TicketStaffView()
		buttons = [
			discord.ui.Button(
				label=button.label,
				style=button.style,
				emoji=button.emoji,
				custom_id=button.custom_id,
				disabled=button.disabled,
			)
			for button in legacy_view.children
			if isinstance(button, discord.ui.Button)
		]
		for source, target in zip(
			[button for button in legacy_view.children if isinstance(button, discord.ui.Button)],
			buttons,
		):
			target.callback = source.callback

		rows = [
			discord.ui.ActionRow(*buttons[0:3]),
			discord.ui.ActionRow(*buttons[3:5]),
			discord.ui.ActionRow(*buttons[5:7]),
			discord.ui.ActionRow(*buttons[7:]),
		]
		self.add_item(
			discord.ui.Container(
				discord.ui.TextDisplay(
					"**Opções exclusivas para os responsáveis pelo atendimento**\n"
					"Use os botões abaixo para gerenciar este ticket."
				),
				discord.ui.Separator(),
				*rows,
				accent_color=BRAND_COLOR,
			)
		)


class CallMemberModal(discord.ui.Modal, title="Chamar membro"):
	mensagem = discord.ui.TextInput(
		label="Mensagem para o membro",
		placeholder="Ex.: Um responsável já irá atender você.",
		max_length=300,
	)

	async def on_submit(self, interaction: discord.Interaction) -> None:
		if not await require_staff(interaction):
			return
		channel = interaction.channel
		if isinstance(channel, discord.TextChannel):
			owner_id = get_ticket_owner_id(channel)
			if owner_id is None:
				await interaction.response.send_message(
					"Não foi possível identificar o autor do ticket.", ephemeral=True
				)
				return
			await interaction.response.send_message(
				f"🔔 <@{owner_id}> {self.mensagem.value}"
			)


class MemberAccessModal(discord.ui.Modal):
	usuario = discord.ui.TextInput(
		label="ID ou menção do usuário",
		placeholder="Ex.: 123456789012345678 ou @usuario",
		max_length=30,
	)

	def __init__(self, title: str, add: bool) -> None:
		super().__init__(title=title)
		self.add = add

	async def on_submit(self, interaction: discord.Interaction) -> None:
		if not await require_staff(interaction):
			return
		member_id = get_member_id(self.usuario.value)
		channel = interaction.channel
		if not member_id or not isinstance(channel, discord.TextChannel) or not interaction.guild:
			await interaction.response.send_message("Não encontrei um ID válido.", ephemeral=True)
			return
		member = interaction.guild.get_member(member_id)
		if not member:
			try:
				member = await interaction.guild.fetch_member(member_id)
			except discord.NotFound:
				member = None
		if not member:
			await interaction.response.send_message("Usuário não encontrado neste servidor.", ephemeral=True)
			return
		if self.add:
			await channel.set_permissions(member, view_channel=True, send_messages=True, read_message_history=True)
			message = f"➕ {member.mention} foi adicionado ao ticket."
		else:
			await channel.set_permissions(member, overwrite=None)
			message = f"❌ {member.mention} foi removido do ticket."
		await interaction.response.send_message(message)


class RenameTicketModal(discord.ui.Modal, title="Trocar nome do canal"):
	nome = discord.ui.TextInput(label="Novo nome", max_length=90)

	async def on_submit(self, interaction: discord.Interaction) -> None:
		if not await require_staff(interaction) or not isinstance(interaction.channel, discord.TextChannel):
			return
		new_name = re.sub(r"[^a-zA-Z0-9-]+", "-", self.nome.value).strip("-").lower()
		if not new_name:
			await interaction.response.send_message("Informe um nome válido.", ephemeral=True)
			return
		await interaction.channel.edit(name=new_name[:100])
		await interaction.response.send_message(f"📝 Canal renomeado para `#{new_name[:100]}`.", ephemeral=True)


class MoveTicketModal(discord.ui.Modal, title="Mover ticket"):
	categoria = discord.ui.TextInput(label="Nome da categoria", placeholder="Ex.: Suporte")

	async def on_submit(self, interaction: discord.Interaction) -> None:
		if not await require_staff(interaction) or not interaction.guild or not isinstance(interaction.channel, discord.TextChannel):
			return
		category = discord.utils.get(interaction.guild.categories, name=self.categoria.value)
		if not category:
			await interaction.response.send_message("Categoria não encontrada.", ephemeral=True)
			return
		await interaction.channel.edit(category=category)
		await interaction.response.send_message(f"🔄 Ticket movido para **{category.name}**.", ephemeral=True)


class NoteModal(discord.ui.Modal, title="Observação interna"):
	nota = discord.ui.TextInput(label="Observação", style=discord.TextStyle.paragraph, max_length=1000)

	async def on_submit(self, interaction: discord.Interaction) -> None:
		if await require_staff(interaction):
			await interaction.response.send_message(
				f"📋 Observação registrada apenas para você:\n> {self.nota.value}",
				ephemeral=True,
			)


class ResultModal(discord.ui.Modal, title="Preset de resultados"):
	resultado = discord.ui.TextInput(label="Resultado do atendimento", style=discord.TextStyle.paragraph, max_length=1000)

	async def on_submit(self, interaction: discord.Interaction) -> None:
		if await require_staff(interaction):
			embed = discord.Embed(title="⚖️ Resultado do atendimento", description=self.resultado.value, color=discord.Color.green())
			await interaction.response.send_message(embed=embed)


class TransferModal(discord.ui.Modal, title="Transferir atendimento"):
	usuario = discord.ui.TextInput(label="ID ou menção do responsável", max_length=30)

	async def on_submit(self, interaction: discord.Interaction) -> None:
		if not await require_staff(interaction) or not interaction.guild:
			return
		member_id = get_member_id(self.usuario.value)
		member = interaction.guild.get_member(member_id) if member_id else None
		if not member or not is_support_or_admin(member):
			await interaction.response.send_message("Informe um membro da equipe de suporte.", ephemeral=True)
			return
		await interaction.response.send_message(f"🔄 Atendimento transferido para {member.mention}.")


async def delete_bot_messages(channel: discord.TextChannel) -> None:
	try:
		await channel.purge(
			limit=100,
			check=lambda message: message.author == bot.user,
			reason="Substituindo mensagem anterior do sistema de tickets",
		)
	except discord.Forbidden:
		print(f"Sem permissão para limpar mensagens antigas em #{channel.name}.")


async def delete_previous_panel(channel: discord.abc.GuildChannel) -> None:
	if not isinstance(channel, discord.TextChannel):
		return
	try:
		async for message in channel.history(limit=100):
			if message.author == bot.user and message.embeds:
				embed = message.embeds[0]
				is_panel = (
					embed.title and embed.title.startswith("Atendimento ")
				) or (
					embed.author and embed.author.name.startswith("Atendimento ")
				)
				if is_panel:
					await message.delete()
					break
	except discord.Forbidden:
		print(f"Sem permissão para substituir o painel em #{channel.name}.")


@bot.tree.command(name="ticket", description="Gerencia o sistema de tickets")
@app_commands.describe(acao="A ação que deseja executar")
@app_commands.choices(
	acao=[
		app_commands.Choice(name="Publicar painel", value="painel"),
	]
)
async def ticket_command(
	interaction: discord.Interaction, acao: app_commands.Choice[str]
) -> None:
	if not interaction.guild or not isinstance(interaction.user, discord.Member):
		await interaction.response.send_message(
			"Este comando só pode ser usado dentro de um servidor.", ephemeral=True
		)
		return
	if not is_support_or_admin(interaction.user):
		await interaction.response.send_message(
			"Você precisa ser administrador ou ter o cargo de suporte para isso.",
			ephemeral=True,
		)
		return

	embed = discord.Embed(
		description=(
			"> Sistema de Tickets para atendimentos aos jogadores\n\n"
			"> Não abra tickets sem Necessidade.\n\n"
			"> Clique no menu abaixo para abrir um ticket"
		),
		color=BRAND_COLOR,
	)
	if interaction.guild.icon:
		embed.set_author(
			name=f"Atendimento {interaction.guild.name}",
			icon_url=interaction.guild.icon.url,
		)
		embed.set_thumbnail(url=interaction.guild.icon.url)
	embed.add_field(
		name="Como funciona?",
		value=(
			"1. Escolha uma categoria no menu abaixo.\n"
			"2. Preencha as informações do atendimento.\n"
			"3. Aguarde um responsável da equipe."
		),
		inline=False,
	)
	embed.set_footer(text="Atendimento organizado • escolha uma categoria para começar")
	if TICKET_BANNER_URL:
		embed.set_image(url=TICKET_BANNER_URL)
	await delete_previous_panel(interaction.channel)
	await interaction.channel.send(
		embed=embed,
		view=TicketPanelView(),
	)
	await interaction.response.send_message("Painel de tickets publicado.", ephemeral=True)


if __name__ == "__main__":
	if not TOKEN:
		raise RuntimeError("Defina a variável de ambiente DISCORD_TOKEN antes de iniciar.")
	bot.run(TOKEN)
