import discord
import random
import asyncio
import concurrent
import logging
import inspect
from typing import Dict, Literal
from discord.ext import commands
from discord import app_commands
import pygsheets
import json
import time
import numpy as np

# Constants based on event spreadsheet template
DRAFTER_COL = 10
DRAFT_FIRST_ROW = 5
MAX_NUM_DRAFTERS = 8
TEAMS_COL = 2
TEAMS_FIRST_ROW = 4
MAX_NUM_TEAMS = 100
EVENT_ID_CELL = 'C2'


class EventDraft:
    def __init__(self, event_page: pygsheets.Worksheet, draft_channel):
        logging.log(logging.DEBUG,
                    f"Starting draft in channel {draft_channel.name} using sheet page {event_page.title}")
        self.draft_channel = draft_channel
        self.event_page = event_page
        self.teams_left = event_page.get_values((TEAMS_FIRST_ROW, TEAMS_COL),
                                                (TEAMS_FIRST_ROW + MAX_NUM_TEAMS, TEAMS_COL))
        self.teams_left = [int(team[0]) for team in self.teams_left]
        self.drafter_names = event_page.get_values((DRAFT_FIRST_ROW, DRAFTER_COL),
                                                   (DRAFT_FIRST_ROW + MAX_NUM_DRAFTERS, DRAFTER_COL))
        self.drafter_names = [names[0] for names in self.drafter_names]
        self.num_picks = 3

    async def run_draft(self):
        num_drafters = len(self.drafter_names)
        draft_list_str = '\n'.join([f'{idx+1}. {name}' for idx,name in enumerate(self.drafter_names)])
        await self.draft_channel.send(f'Draft order is:\n{draft_list_str}')
        for pick_num in range(self.num_picks):
            if pick_num % 2 == 1:
                draft_order = reversed(range(num_drafters))
            else:
                draft_order = range(num_drafters)

            for drafter_idx in draft_order:
                drafter_name = self.drafter_names[drafter_idx]
                drafter_username = name_to_discord_username[drafter_name]
                logger.debug(f"{self.draft_channel.members=} {drafter_username[0]=} {drafter_username[1]=}")
                drafter_user = discord.utils.get(self.draft_channel.members, name=drafter_username[0],
                                                 discriminator=drafter_username[1])

                await self.draft_channel.send(
                    f'Current drafter is {drafter_user.mention}')

                # Create the view containing our dropdown
                view = DropdownView(self.teams_left)

                # Sending a message containing our view
                pick_msg = await drafter_user.send(f'It is your pick for {self.event_page.title} (pick #{pick_num + 1}):', view=view)

                # Wait for value from team picker callback
                await view.dropdown.pick_team_num_future
                picked_team_num = view.dropdown.pick_team_num_future.result()

                # Replace picker so multiple teams cannot be selected and to provide feedback of success
                await pick_msg.edit(content=f"You selected team **{picked_team_num}** for {self.event_page.title} pick #{pick_num + 1}", view=None)
                del view.dropdown.pick_team_num_future

                logging.log(logging.DEBUG, f"{drafter_name} picked {picked_team_num}")
                self.event_page.update_value((DRAFT_FIRST_ROW + drafter_idx, DRAFTER_COL + 1 + pick_num * 2),
                                             str(picked_team_num))
                self.teams_left.remove(picked_team_num)

                await self.draft_channel.send(f'{drafter_user.nick} has picked team {picked_team_num}')
        logger.info("Draft has finished!")
        await self.draft_channel.send(f'@everyone Draft for {self.event_page.title} has finished!\nSee completed event page below:\n{self.event_page.url}')

    # def stop_draft(self):
    #     print(f"Ending draft {self.event_id}")


class Dropdown(discord.ui.Select):
    def __init__(self, teams_left: list[int]):
        self.pick_team_num_future = asyncio.get_event_loop().create_future()
        # Set the options that will be presented inside the dropdown
        options = [discord.SelectOption(label=str(team_num)) for team_num in teams_left]
        options = options[:25]

        # options = [
        #     discord.SelectOption(label='Red', description='Your favourite colour is red', emoji='🟥'),
        #     discord.SelectOption(label='Green', description='Your favourite colour is green', emoji='🟩'),
        #     discord.SelectOption(label='Blue', description='Your favourite colour is blue', emoji='🟦'),
        # ]
        # The placeholder is what will be shown when no option is chosen
        # The min and max values indicate we can only pick one of the three options
        # The options parameter defines the dropdown options. We defined this above
        super().__init__(placeholder='Select your next pick:', min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        team_num = int(self.values[0])

        # await interaction.delete_original_response()
        self.pick_team_num_future.set_result(team_num)


class DropdownView(discord.ui.View):
    def __init__(self, teams_left: list[int]):
        super().__init__()

        # Adds the dropdown to our view object.
        self.dropdown = Dropdown(teams_left)
        self.add_item(self.dropdown)


class FirstBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True

        super().__init__(command_prefix=commands.when_mentioned_or('__'), intents=intents)

        self.current_drafts: Dict[str, EventDraft] = {}

    _bot_instance = None

    @classmethod
    async def CreateFirstBot(cls):
        if cls._bot_instance:
            return cls._bot_instance
        bot_instance = FirstBot()
        await bot_instance.add_commands()
        return bot_instance

    async def on_ready(self):
        await self.wait_until_ready()
        print(f'Logged in as {self.user} (ID: {self.user.id})')
        print('------')
        await self.tree.sync()

    async def setup_hook(self) -> None:
        logger.debug(f"Hook")
        # Sync the application command with Discord.
        await self.tree.sync()

    async def add_commands(self):
        members = inspect.getmembers(self)
        for name, member in members:
            if isinstance(member, commands.Command):
                if member.parent is None:
                    logger.debug(name)
                    self.add_command(member)

    # @commands.hybrid_command()
    # async def start_draft(self, ctx: commands.Context, event_id: str):
    #     """Starts a Fantasy FIRST draft with the given event ID"""
    #     print(f"{ctx.guild=}")
    #     if event_id in self.current_drafts:
    #         await ctx.send(f'Draft already exists for event **{event_id}**, use `/stop_draft {event_id}` to end draft')
    #         return
    #
    #     await ctx.send(f'Starting Fantasy FIRST draft for event **{event_id}**')
    #     event_page = event_map[event_id]
    #
    #     # Handle sheet data loading and draft var resets
    #     draft = EventDraft(event_page, ctx)
    #
    #     self.current_drafts[event_id] = draft
    #     await draft.run_draft()
    #
    # @commands.hybrid_command()
    # async def stop_draft(self, ctx: commands.Context, event_id: str):
    #     """Stops a Fantasy FIRST draft with the given event ID"""
    #
    #     if event_id not in self.current_drafts:
    #         await ctx.send(
    #             f'No current draft for event **{event_id}**, use `/start_draft {event_id}` to start draft')
    #         return
    #
    #     self.current_drafts.pop(event_id).stop_draft()
    #     await ctx.send(f' Stopped draft for event **{event_id}**')

    def load_discord_usernames(self):
        pass

    # Per Event functions
    def load_teams(self):
        pass

    def send_pick(self):
        pass


with open("keys/name_to_discord_usernames.json", "r") as names_file:
    name_to_discord_username = json.load(names_file)

with open("keys/FF-token.txt", "r") as token_file:
    token = token_file.readline()[:-1]

# Set up logging
logger = logging.getLogger('fantasy_first')
logger.setLevel(logging.DEBUG)
handler = logging.FileHandler(filename='fantasy_first.log', encoding='utf-8', mode='w')
dt_fmt = '%Y-%m-%d %H:%M:%S'
formatter = logging.Formatter('[{asctime}] [{levelname:<8}] {name}: {message}', dt_fmt, style='{')

handler.setFormatter(formatter)
logger.addHandler(handler)

client = pygsheets.authorize(service_account_file="keys/fantasy-first-test-372522-4d15a60bbdcb.json")
sheet = client.open('Copy of 2023 FF')
all_pages = sheet.worksheets()
excluded_pages = {"Master Score Sheet", "Event Template", "Old Event Template", "NE Top 16 Predictions"}
# event_ids = {'2023nhgrs', '2023mabr', '2023rinsc', '2023ctwat', '2023marea', ''}

event_pages = filter(lambda page: page.title not in excluded_pages, all_pages)
event_map: dict[str, pygsheets.Worksheet] = {event_page.cell(EVENT_ID_CELL).value: event_page for event_page in
                                             event_pages}

# async def start():
#     bot = await
#     # bot = FirstBot()
#     # await bot.sync()

loop = asyncio.get_event_loop()
bot = loop.run_until_complete(FirstBot.CreateFirstBot())


@bot.hybrid_command()
async def test(ctx: commands.Context):
    """Test command"""
    logger.debug("Test command")
    await ctx.send(f' Test Command')


@bot.hybrid_command(name='sync', description='Owner only')
async def sync(ctx: commands.Context):
    await bot.tree.sync()
    logger.debug('Command tree synced.')
    await ctx.send(f' Command tree synced')


# options = [discord.SelectOption(label=str(team_num)) for team_num in teams_left]


# @bot.hybrid_command()
@bot.tree.command()
# @app_commands.command()
# @app_commands.choices(event_ids=[app_commands.Choice(name=event_id, value=event_id) for event_id in event_map.keys()])
async def start_draft(interaction: discord.Interaction, event_id: Literal[tuple(event_map.keys())]):
    """Starts a Fantasy FIRST draft with the given event ID"""
    logger.debug(f"{interaction.guild=}")
    if event_id in bot.current_drafts:
        await interaction.response.send_mesage(f'Draft already exists for event **{event_id}**, use `/stop_draft {event_id}` to end draft')
        return

    await interaction.channel.send(f'Starting Fantasy FIRST draft for event **{event_id}**')
    event_page = event_map[event_id]

    # Handle sheet data loading and draft var resets
    draft = EventDraft(event_page, interaction.channel)

    bot.current_drafts[event_id] = draft
    await draft.run_draft()


@bot.hybrid_command()
async def stop_draft(ctx: commands.Context, event_id: str):
    """Stops a Fantasy FIRST draft with the given event ID"""

    if event_id not in bot.current_drafts:
        await ctx.send(
            f'No current draft for event **{event_id}**, use `/start_draft {event_id}` to start draft', ephemeral=True)
        return

    bot.current_drafts.pop(event_id).stop_draft()
    await ctx.send(f' Stopped draft for event **{event_id}**')


print("Running bot")
bot.run(token, log_handler=handler)
