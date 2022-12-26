# This example requires the 'message_content' intent.
from discord.ext import commands
import discord
import random
import asyncio
import logging
import inspect
from typing import Dict

SERVER = discord.Object(0)


# Defines a custom Select containing colour options
# that the user can choose. The callback function
# of this class is called when the user changes their choice
class Dropdown(discord.ui.Select):
    def __init__(self):
        # Set the options that will be presented inside the dropdown
        options = [
            discord.SelectOption(label='Red', description='Your favourite colour is red', emoji='🟥'),
            discord.SelectOption(label='Green', description='Your favourite colour is green', emoji='🟩'),
            discord.SelectOption(label='Blue', description='Your favourite colour is blue', emoji='🟦'),
        ]

        # The placeholder is what will be shown when no option is chosen
        # The min and max values indicate we can only pick one of the three options
        # The options parameter defines the dropdown options. We defined this above
        super().__init__(placeholder='Choose your favourite colour...', min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        # Use the interaction object to send a response message containing
        # the user's favourite colour or choice. The self object refers to the
        # Select object, and the values attribute gets a list of the user's
        # selected options. We only want the first one.
        await interaction.response.send_message(f'Your favourite colour is {self.values[0]}', ephemeral=False)


class DropdownView(discord.ui.View):
    def __init__(self):
        super().__init__()

        # Adds the dropdown to our view object.
        self.add_item(Dropdown())


class EventDraft:
    def __init__(self, event_id, channel: commands.Context):
        self.event_id = event_id
        self.channel = channel

    def stop_draft(self):
        print(f"Ending draft {self.event_id}")


class FirstBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True

        super().__init__(command_prefix=commands.when_mentioned_or('__'), intents=intents)

        self.current_drafts: Dict[str, EventDraft] = {}

    _bot_instance = None

    @classmethod
    async def CreateFirstBot(cls):
        if cls._bot_instance:
            return cls._bot_instance
        bot_instance = FirstBot()
        await bot_instance.add_commands()

    async def on_ready(self):
        print(f'Logged in as {self.user} (ID: {self.user.id})')
        print('------')

    async def setup_hook(self) -> None:
        # Sync the application command with Discord.
        await self.tree.sync()

    async def sync(self):
        await self.tree.sync()

    async def add_commands(self):
        members = inspect.getmembers(self)
        for name, member in members:
            if isinstance(member, commands.Command):
                if member.parent is None:
                    print(name)
                    self.add_command(member)
        await self.sync()

    @commands.hybrid_command()
    async def start_draft(self, ctx: commands.Context, event_id: str):
        """Starts a Fantasy FIRST draft with the given event ID"""
        print(f"{ctx.guild=}")
        if event_id in self.current_drafts:
            await ctx.send(f'Draft already exists for event **{event_id}**, use `/stop_draft {event_id}` to end draft')
            return

        await ctx.send(f'Starting Fantasy FIRST draft for event **{event_id}**')

        # Handle sheet data loading and draft var resets
        draft = EventDraft(event_id, ctx)

        self.current_drafts[event_id] = draft

        # Create the view containing our dropdown
        view = DropdownView()

        # Sending a message containing our view
        await ctx.send('Pick your favourite colour:', view=view, ephemeral=True)

    @commands.hybrid_command()
    async def stop_draft(self, ctx: commands.Context, event_id: str):
        """Stops a Fantasy FIRST draft with the given event ID"""

        if event_id not in self.current_drafts:
            await ctx.send(
                f'No current draft for event **{event_id}**, use `/start_draft {event_id}` to start draft')
            return

        self.current_drafts.pop(event_id).stop_draft()
        await ctx.send(f' Stopped draft for event **{event_id}**')

    def load_discord_usernames(self):
        pass

    # Per Event functions
    def load_teams(self):
        pass

    def send_pick(self):
        pass


with open("keys/FF-token.txt", "r") as token_file:
    token = token_file.readline()[:-1]

handler = logging.FileHandler(filename='discord.log', encoding='utf-8', mode='w')

# client = MyClient(intents=intents)
# client.run(token, log_handler=handler)

bot = FirstBot()
bot.users
bot.run(token)

# my_bot = MyBot("/", intents=intents)
# my_bot.run(token, log_handler=handler)

# MTA1NTMyMDA1NzMwMDkxNDIyNw.G73Hij.kK8pkXluSyLfArxIwerHgv-FM54it4byVzBF9g
