import sys
import typing

import discord
import random
import asyncio
# import concurrent
import logging
import inspect
from typing import Dict, Literal
from discord.ext import commands
from discord import app_commands
import pygsheets
import json
import time
import numpy as np
import argparse

# Constants based on event spreadsheet template
DRAFTER_COL = 10
DRAFT_FIRST_ROW = 5
MAX_NUM_DRAFTERS = 8
TEAMS_COL = 2
TEAMS_FIRST_ROW = 4
TEAM_NAME_COL = 3
MAX_NUM_TEAMS = 100
EVENT_ID_CELL = 'C2'
MAX_DISCORD_SELECTORS = 25

ADMIN_ROLE_NAME = "bot admin"
AVATAR_FILEPATH = 'avatar.jpg'

DEBUG_SHEET_NAME = 'Copy of 2023 FF'
SHEET_NAME = '2023 FF'


class EventDraft:
    def __init__(self, event_page: pygsheets.Worksheet, draft_channel: discord.TextChannel):
        self.current_msgs: typing.List[discord.Message] = []
        logging.log(logging.DEBUG,
                    f"Starting draft in channel {draft_channel.name} using sheet page {event_page.title}")
        self.draft_channel = draft_channel
        self.event_page = event_page
        self.teams_left = event_page.get_values((TEAMS_FIRST_ROW, TEAMS_COL),
                                                (TEAMS_FIRST_ROW + MAX_NUM_TEAMS, TEAMS_COL),
                                                value_render=pygsheets.ValueRenderOption.UNFORMATTED_VALUE)
        team_names = event_page.get_values((TEAMS_FIRST_ROW, TEAM_NAME_COL),
                                           (TEAMS_FIRST_ROW + MAX_NUM_TEAMS, TEAM_NAME_COL),
                                           value_render=pygsheets.ValueRenderOption.UNFORMATTED_VALUE)
        # print(event_page.get_values((TEAMS_FIRST_ROW, TEAMS_COL),
        #                                         (TEAMS_FIRST_ROW + MAX_NUM_TEAMS, TEAMS_COL), value_render=pygsheets.ValueRenderOption.UNFORMATTED_VALUE))
        self.all_teams = [team[0] for team in self.teams_left]
        self.teams_left = [team[0] for team in self.teams_left]
        team_names = [name[0] for name in team_names]
        self.team_name_dict = {num: name for num, name in zip(self.all_teams, team_names)}
        self.drafter_names = event_page.get_values((DRAFT_FIRST_ROW, DRAFTER_COL),
                                                   (DRAFT_FIRST_ROW + MAX_NUM_DRAFTERS, DRAFTER_COL))
        self.drafter_names = [names[0] for names in self.drafter_names]
        for name in self.drafter_names:
            if discord.utils.get(self.draft_channel.members, nick=name) is None:
                raise LookupError(f"User \"{name}\" not found in current draft channel, cannot create draft")
        self.num_picks = 3
        self.pick_num = 0
        self.current_drafter_user: discord.Member = None
        self.stop_future = asyncio.get_event_loop().create_future()

    async def run_draft(self):
        num_drafters = len(self.drafter_names)

        start_pick = 0
        for start_pick in range(self.num_picks * num_drafters):
            round_num = start_pick // num_drafters
            drafter_idx = start_pick % num_drafters

            # If it is a reverse order round
            if round_num % 2 == 1:
                drafter_idx = (num_drafters - 1) - drafter_idx
            picked_team_num = self.event_page.get_value(
                (DRAFT_FIRST_ROW + drafter_idx, DRAFTER_COL + 1 + round_num * 2),
                value_render=pygsheets.ValueRenderOption.UNFORMATTED_VALUE)
            if picked_team_num == '':
                break
            self.teams_left.remove(picked_team_num)
        draft_list_str = '\n'.join([f'{idx + 1}. {name}' for idx, name in enumerate(self.drafter_names)])
        draft_order_msg = await self.draft_channel.send(f'Draft order is:\n{draft_list_str}')
        self.current_msgs.append(draft_order_msg)

        teams_left_str = "Teams Left:\n" + "\n".join(
            [f'{team_num}' if team_num in self.teams_left else f'~~{team_num}~~' for team_num in self.all_teams])
        teams_left_msg = await self.draft_channel.send(teams_left_str)
        for self.pick_num in range(start_pick, self.num_picks * num_drafters):
            round_num = self.pick_num // num_drafters
            drafter_idx = self.pick_num % num_drafters
            if round_num % 2 == 1:
                drafter_idx = (num_drafters - 1) - drafter_idx
            drafter_name = self.drafter_names[drafter_idx]

            self.current_drafter_user = discord.utils.get(self.draft_channel.members, nick=drafter_name)
            logger.debug(f"{self.draft_channel.members=} {self.current_drafter_user.name}")
            current_msg = await self.draft_channel.send(
                f'Current drafter is {self.current_drafter_user.nick}')
            self.current_msgs.append(current_msg)

            dropdown_view = DropdownView(self.teams_left)

            next_pick_too = ""
            if self.pick_num % num_drafters == num_drafters - 1 and round_num != self.num_picks - 1:
                next_pick_too = ", you are also picking again next"
            # Sending a message containing our view
            pick_msg = await self.current_drafter_user.send(
                f'It is your pick for {self.event_page.title} (round #{round_num + 1}){next_pick_too}:',
                view=dropdown_view)
            self.current_msgs.append(pick_msg)
            dropdown_view.callback_futures.append(self.stop_future)
            # Wait for value from one of the team pickers' callback
            try:
                done, pending = await asyncio.wait(dropdown_view.callback_futures,
                                                   return_when=asyncio.FIRST_COMPLETED)
            except KeyboardInterrupt as e:
                logger.info(f"Interrupt signal sent, shutting down")
                await pick_msg.delete()
                logger.debug(f"Message deleted")
                sys.exit()
            if self.stop_future in done:
                print("Stopping draft")
                logger.info("Stopping draft")

                # TODO Add cleanup function
                await self.cleanup_messages()
                # await pick_msg.delete()
                # await draft_order_msg.delete()
                # await teams_left_msg.delete()
                # await current_msg.delete()
                return
            logging.debug(f"{done=}")
            picked_team_num = list(done)[0].result()

            # Replace picker so multiple teams cannot be selected and to provide feedback of successful pick
            await pick_msg.edit(
                content=f"You selected team **{picked_team_num}** for {self.event_page.title} round #{round_num + 1}",
                view=None)

            self.current_msgs.pop()  # Pick msg
            self.current_msgs.pop()  # Remove previous drafter message from stack
            # TODO clean up futures

            logging.log(logging.DEBUG, f"{drafter_name} picked {picked_team_num}")
            self.event_page.update_value((DRAFT_FIRST_ROW + drafter_idx, DRAFTER_COL + 1 + round_num * 2),
                                         str(picked_team_num))
            self.teams_left.remove(picked_team_num)
            teams_left_str = "Teams Left:\n" + "\n".join([
                f'{team_num}' if team_num in self.teams_left else f'~~{team_num}~~' for team_num in self.all_teams])
            await teams_left_msg.edit(content=teams_left_str)

            await self.draft_channel.send(f'{self.current_drafter_user.nick} has picked team {picked_team_num}')
        logger.info("Draft has finished!")
        await self.draft_channel.send(
            f'@everyone Draft for {self.event_page.title} has finished!\nSee completed event page below:\n{self.event_page.url}')

    async def cleanup_messages(self):
        for msg in self.current_msgs:
            await msg.delete()
        self.current_msgs.clear()

    def stop_draft(self):
        self.stop_future.set_result("Stop")


class DropdownView(discord.ui.View):
    def __init__(self, teams_left: list[int]):
        super().__init__(timeout=None)

        # Create the view containing our dropdown
        self.callback_futures = []

        for i, team_index in enumerate(
                range(0, len(teams_left), 25)):  # 25 is the max number of options in a dropdown on Discord
            # Adds the dropdown to our view object.
            curr_dropdown = Dropdown(teams_left[team_index:team_index + 25], row=i)
            self.add_item(curr_dropdown)
            self.callback_futures.append(curr_dropdown.pick_team_num_future)
            # self.dropdown = Dropdown(teams_left, row)


class Dropdown(discord.ui.Select):
    def __init__(self, teams_left: list[int], row=0):
        self.pick_team_num_future = asyncio.get_event_loop().create_future()
        # Set the options that will be presented inside the dropdown
        options = [discord.SelectOption(label=str(team_num)) for team_num in teams_left]
        options = options[:25]

        super().__init__(placeholder=f'Select your next pick: ({teams_left[0]} - {teams_left[-1]})', min_values=1,
                         max_values=1, options=options, row=row)

    async def callback(self, interaction: discord.Interaction):
        team_num = int(self.values[0])

        # await interaction.delete_original_response()
        self.pick_team_num_future.set_result(team_num)


class FirstBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True

        super().__init__(command_prefix=commands.when_mentioned_or('__'), intents=intents)

        self.current_drafts: Dict[str, EventDraft] = {}

    _bot_instance = None

    @classmethod
    async def create_first_bot(cls):
        if cls._bot_instance:
            return cls._bot_instance
        bot_instance = FirstBot()
        await bot_instance.add_commands()
        return bot_instance

    async def on_ready(self):
        await self.wait_until_ready()
        print(f'Logged in as {self.user} (ID: {self.user.id})')
        print('------')
        with open(AVATAR_FILEPATH, 'rb') as image:
            await self.user.edit(avatar=image.read())
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


parser = argparse.ArgumentParser()
parser.add_argument("-d", "--debug", action='store_true')
args = parser.parse_args()

if args.debug:
    sheet_name = DEBUG_SHEET_NAME
else:
    sheet_name = SHEET_NAME

# TODO Remove
# with open("keys/name_to_discord_usernames.json", "r") as names_file:
#     name_to_discord_username = json.load(names_file)

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
sheet = client.open(sheet_name)
all_pages = sheet.worksheets()
excluded_pages = {"Master Score Sheet", "Event Template", "Old Old Event Template", "NE Top 16 Predictions", "Rules",
                  "Draft Order Roll", "Old [2022] Event Template"}
# event_ids = {'2023nhgrs', '2023mabr', '2023rinsc', '2023ctwat', '2023marea', ''}

event_pages = filter(lambda page: page.title not in excluded_pages, all_pages)
event_map: dict[str, pygsheets.Worksheet] = {event_page.cell(EVENT_ID_CELL).value: event_page for event_page in
                                             event_pages}

# async def start():
#     bot = await
#     # bot = FirstBot()
#     # await bot.sync()

# loop = asyncio.new_event_loop()
bot: FirstBot = asyncio.run(FirstBot.create_first_bot())


@bot.hybrid_command(name='sync', description='Bot Admins only')
@commands.has_role("Bot Admin")
async def sync(ctx: commands.Context):
    await bot.tree.sync()
    logger.debug('Command tree synced.')
    await ctx.send(f' Command tree synced')


# options = [discord.SelectOption(label=str(team_num)) for team_num in teams_left]

# TODO Change to use command.check()
# @bot.hybrid_command()
@bot.tree.command()
# @app_commands.command()
# @app_commands.choices(event_ids=[app_commands.Choice(name=event_id, value=event_id) for event_id in event_map.keys()])
@commands.has_role(ADMIN_ROLE_NAME)
async def start_draft(interaction: discord.Interaction, event_id: Literal[tuple(event_map.keys())]):
    """Starts a Fantasy FIRST draft with the given event ID"""
    if event_id in bot.current_drafts:
        await interaction.response.send_message(
            f'Draft already exists for event **{event_id}**, use `/stop_draft {event_id}` to end draft', ephemeral=True)
        return

    if not isinstance(interaction.channel, discord.TextChannel):
        logger.info(f"Start draft must be invoked in a text channel")
        await interaction.response.send_message(f"Start draft must be invoked in a text channel", ephemeral=True)
        return
    bot_admin_role = discord.utils.get(interaction.guild.roles, name=ADMIN_ROLE_NAME)
    if bot_admin_role not in interaction.user.roles:
        await interaction.response.send_message(f"Only bot admins can invoke this command", ephemeral=True)
        return

    logger.debug(f"{interaction.guild=}")

    event_page = event_map[event_id]

    # Handle sheet data loading and draft var resets
    try:
        draft = EventDraft(event_page, interaction.channel)
    except LookupError as err:
        await interaction.response.send_message(str(err), ephemeral=True)
        return

    await interaction.response.send_message(f'Starting Fantasy FIRST draft for event **{event_id}**')

    bot.current_drafts[event_id] = draft
    await draft.run_draft()
    del bot.current_drafts[event_id]


@bot.tree.command()
async def stop_draft(interaction: discord.Interaction, event_id: Literal[tuple(event_map.keys())]):
    """Stops a Fantasy FIRST draft with the given event ID"""
    bot_admin_role = discord.utils.get(interaction.guild.roles, name=ADMIN_ROLE_NAME)
    if bot_admin_role not in interaction.user.roles:
        await interaction.response.send_message(f"Only bot admins can invoke this command", ephemeral=True)
        return

    if event_id not in bot.current_drafts:
        await interaction.response.send_message(
            f'No current draft for event **{event_id}**, use `/start_draft {event_id}` to start draft', ephemeral=True)
        return

    bot.current_drafts[event_id].stop_draft()
    await interaction.response.send_message(f' Stopped draft for event **{event_id}**')


@bot.hybrid_command()
async def list_drafts(ctx: commands.Context):
    """Prints list of in-progress drafts"""
    msg_string = "**Current drafts**\n"
    for event_id, draft in bot.current_drafts.items():
        msg_string += f" - {event_id}: Round #{(draft.pick_num // len(draft.drafter_names)) + 1} ({draft.current_drafter_user.nick})\n"
    await ctx.send(msg_string)


print("Running bot")
bot.run(token, log_handler=handler)
