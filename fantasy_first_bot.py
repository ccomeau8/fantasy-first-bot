import datetime
import pprint
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

from collections import defaultdict

# Constants based on event spreadsheet template
DRAFTER_COL = 10
DRAFT_FIRST_ROW = 5
MAX_NUM_DRAFTERS = 8
TEAMS_COL = 2
TEAMS_FIRST_ROW = 4
TEAM_NAME_COL = 3
MAX_NUM_TEAMS = 100
EVENT_ID_CELL = 'C2'
DRAFT_END_TIME_CELL = 'E2'
ACTIVE_HOURS_START_TIME_CELL = 'O3'
ACTIVE_HOURS_END_TIME_CELL = 'P3'
MAX_DISCORD_SELECTORS = 25

ADMIN_ROLE_NAME = "bot admin"
AVATAR_FILEPATH = 'avatar.jpg'

DEBUG_SHEET_NAME = 'Copy of 2023 FF'
SHEET_NAME = '2023 FF'

# Date/Time Constants
ACTIVE_HOURS_START_TIME = datetime.time(hour=10)
ACTIVE_HOURS_END_TIME = datetime.time(hour=22)
# Based on https://developers.google.com/sheets/api/reference/rest/v4/DateTimeRenderOption
SHEETS_SERIAL_NUMBER_DATETIME_START = datetime.datetime(1899, 12, 30)
DATE_STRING_WIDTH = 21
PICK_DEADLINE_SNAP_INTERVAL = 15  # Snaps picks to 15 min intervals, ex. 3:08 -> 3:15


# BASE_TIMEZONE = datetime.timezone.tzname()

class EventDraft:
    def __init__(self, event_id: str, event_page: pygsheets.Worksheet, draft_interaction: discord.Interaction):
        self.current_msgs: typing.List[discord.Message] = []
        self.draft_interaction = draft_interaction
        self.draft_channel: discord.TextChannel = draft_interaction.channel
        logging.log(logging.DEBUG,
                    f"Initializing draft in channel {self.draft_channel.name} using sheet page {event_page.title}")
        self.event_page = event_page
        self.event_id = event_id
        self.teams_left = event_page.get_values((TEAMS_FIRST_ROW, TEAMS_COL),
                                                (TEAMS_FIRST_ROW + MAX_NUM_TEAMS, TEAMS_COL),
                                                value_render=pygsheets.ValueRenderOption.UNFORMATTED_VALUE)
        team_names = event_page.get_values((TEAMS_FIRST_ROW, TEAM_NAME_COL),
                                           (TEAMS_FIRST_ROW + MAX_NUM_TEAMS, TEAM_NAME_COL),
                                           value_render=pygsheets.ValueRenderOption.UNFORMATTED_VALUE)
        raw_draft_end_datetime = self.event_page.get_value(
            DRAFT_END_TIME_CELL,
            value_render=pygsheets.ValueRenderOption.UNFORMATTED_VALUE)
        self.draft_end_datetime = SHEETS_SERIAL_NUMBER_DATETIME_START + datetime.timedelta(days=raw_draft_end_datetime)

        self.all_teams = [team[0] for team in self.teams_left]
        self.teams_left = [team[0] for team in self.teams_left]
        team_names = [name[0] for name in team_names]
        self.team_name_dict = {num: name for num, name in zip(self.all_teams, team_names)}
        self.drafter_names = event_page.get_values((DRAFT_FIRST_ROW, DRAFTER_COL),
                                                   (DRAFT_FIRST_ROW + MAX_NUM_DRAFTERS, DRAFTER_COL))
        self.drafter_names = [names[0] for names in self.drafter_names]
        self.draft_picks: dict[str, list[None | str]] = {}
        self.num_picks = 3
        for name in self.drafter_names:
            if discord.utils.get(self.draft_channel.members, nick=name) is None:
                raise LookupError(f"User \"{name}\" not found in current draft channel, cannot create draft")
            self.draft_picks[name] = [None] * self.num_picks
        self.pick_num = 0
        self.current_drafter_user: discord.Member = None
        self.stop_future = asyncio.get_event_loop().create_future()
        self.skip_button = SkipButton(self)
        self.draft_start_time = None
        self.reminder_msgs = []

    async def run_draft(self):
        num_drafters = len(self.drafter_names)

        picked_teams = []

        self.draft_start_time = datetime.datetime.now()  # TODO Deal with timezones
        if (self.draft_start_time > self.draft_end_datetime):
            # Draft started after deadline, invalid
            await self.draft_interaction.response.send_message(f"Deadline for **{self.event_id}** has passed ({self.draft_end_datetime.strftime('%a. %b %d %Y %I:%M%p')})", ephemeral=True)
            return

        await self.draft_interaction.response.send_message(f'Starting Fantasy FIRST draft for event **{self.event_id}**')

        start_pick = None
        for scan_pick in range(self.num_picks * num_drafters):
            round_num = scan_pick // num_drafters
            drafter_idx = scan_pick % num_drafters

            # If it is a reverse order round
            if round_num % 2 == 1:
                drafter_idx = (num_drafters - 1) - drafter_idx
            drafter_name = self.drafter_names[drafter_idx]
            picked_team_num = self.event_page.get_value(
                (DRAFT_FIRST_ROW + drafter_idx, DRAFTER_COL + 1 + round_num * 2),
                value_render=pygsheets.ValueRenderOption.UNFORMATTED_VALUE)
            if picked_team_num == '':
                if start_pick is None:
                    start_pick = scan_pick
                logger.debug(f"Blank, {picked_team_num} {start_pick} {scan_pick}")
                continue
            self.teams_left.remove(picked_team_num)
            self.draft_picks[drafter_name][round_num] = picked_team_num
            picked_teams.append(picked_team_num)

        logger.info(f"Pre-picked teams: {start_pick} {self.draft_picks}")
        start_active_start_datetime = datetime.datetime.combine(self.draft_start_time.date(), ACTIVE_HOURS_START_TIME)
        start_active_end_datetime = datetime.datetime.combine(self.draft_start_time.date(), ACTIVE_HOURS_END_TIME)
        end_active_start_datetime = datetime.datetime.combine(self.draft_end_datetime.date(), ACTIVE_HOURS_START_TIME)
        end_active_end_datetime = datetime.datetime.combine(self.draft_end_datetime.date(), ACTIVE_HOURS_END_TIME)
        self.draft_end_datetime = min(end_active_end_datetime, self.draft_end_datetime)

        day_lengths = []
        start_day_start_time = min(start_active_end_datetime, max(start_active_start_datetime, self.draft_start_time))
        end_day_start_time = min(end_active_end_datetime, max(end_active_start_datetime, self.draft_end_datetime))
        if self.draft_start_time.date() == self.draft_end_datetime.date():  # Draft starts and ends on same day
            start_day_time_duration = end_day_start_time - start_day_start_time
            day_lengths.append(start_day_time_duration)
            print(f"END {end_day_start_time} || {start_day_start_time}")
        else:
            start_day_time_duration = start_active_end_datetime - start_day_start_time
            day_lengths.append(start_day_time_duration)
            print(f"START {start_day_start_time} || {start_active_end_datetime}")
            # logger.info(f"End day: {self.draft_end_datetime.day} {self.draft_start_time.day}")
            num_full_days = max(0,
                                (self.draft_end_datetime.date() - self.draft_start_time.date()).days + 1 - 2)  # N days minus start and end days
            full_active_duration = start_active_end_datetime - start_active_start_datetime

            day_lengths.extend([full_active_duration] * num_full_days)
            end_day_time_duration = end_day_start_time - end_active_start_datetime
            day_lengths.append(end_day_time_duration)

        total_draft_time = sum(day_lengths, datetime.timedelta(0))

        print(f"TOTAL draft time {total_draft_time} | {day_lengths}")

        num_picks_left = self.num_picks * num_drafters - len(picked_teams)
        time_per_pick = total_draft_time / num_picks_left

        pick_deadlines = []
        day_idx = 0
        day_total = datetime.timedelta(0)

        for pick_num in range(1, num_picks_left + 1):
            pick_end_duration_point = pick_num * time_per_pick
            print(pick_num, pick_end_duration_point, day_idx)
            while day_total + day_lengths[day_idx] < pick_end_duration_point - datetime.timedelta(
                    seconds=1):  # Account for floating point math
                day_total += day_lengths[day_idx]
                day_idx += 1
                print(f"New day {day_idx}")
            intraday_pick_duration = pick_end_duration_point - day_total

            if day_idx == 0:
                current_day_start_time = start_day_start_time
            else:
                current_day_start_time = datetime.datetime.combine(
                    self.draft_start_time.date() + datetime.timedelta(days=day_idx), ACTIVE_HOURS_START_TIME)

            pick_time = current_day_start_time + intraday_pick_duration
            pick_deadlines.append(pick_time)

        # Round pick deadlines to nearest 15 min
        for i, deadline in enumerate(pick_deadlines):
            discard = datetime.timedelta(minutes=deadline.minute % PICK_DEADLINE_SNAP_INTERVAL,
                                         seconds=deadline.second,
                                         microseconds=deadline.microsecond)
            deadline -= discard
            if discard >= datetime.timedelta(minutes=PICK_DEADLINE_SNAP_INTERVAL / 2.0):
                deadline += datetime.timedelta(minutes=PICK_DEADLINE_SNAP_INTERVAL)
            pick_deadlines[i] = deadline


        logger.info(f"Deadlines: {pick_deadlines}")
        max_len_name = max([len(name) for name in self.drafter_names])

        time_msg_str = f"Draft Start Time: {self.draft_start_time.strftime('%a. %b %d %I:%M%p')}\n"
        time_msg_str += f"Draft End Time: {self.draft_end_datetime.strftime('%a. %b %d %I:%M%p')}\n"
        time_msg_str += f"Minimum Time Limit per Pick:  {(str(time_per_pick.days) + 'd ') if time_per_pick.days > 0 else ''}{time_per_pick.seconds // 3600}hr {(time_per_pick.seconds % 3600) // 60:0>2}min\n"

        pick_cell_str_table = defaultdict(lambda: [])
        pick_num = 0

        for pick_idx in range(self.num_picks * num_drafters):
            round_num = pick_idx // num_drafters
            drafter_idx = pick_idx % num_drafters
            if round_num % 2 == 1:
                drafter_idx = (num_drafters - 1) - drafter_idx
            drafter_name = self.drafter_names[drafter_idx]

            # for round_num in range(self.num_picks):
                # pick_num = round_num * num_drafters + (
                #     drafter_idx if round_num % 2 == 0 else (num_drafters - 1) - drafter_idx)
            if self.draft_picks[drafter_name][round_num]:
                cell_str = f"|{self.draft_picks[drafter_name][round_num]:^{DATE_STRING_WIDTH}}"
            else:
                deadline = pick_deadlines[pick_num]
                cell_str = f"| {deadline.strftime('%a. %b %d %I:%M%p')} "
                pick_num += 1

            pick_cell_str_table[drafter_name].append(cell_str)


        pprint.pp(pick_cell_str_table)
        round_title_str = '|'.join(
            [f"{f'Round {round_num + 1}':^{DATE_STRING_WIDTH}}" for round_num in range(self.num_picks)])
        title_line = f"{'Name':^{max_len_name + 1}}|{round_title_str}"
        title_line = f"```\n{title_line}\n{'-' * len(title_line)}\n"

        pick_table_str = f"{title_line}"
        for drafter_name in self.drafter_names:
            row_str = f"{drafter_name:>{max_len_name}} "
            for round_num in range(self.num_picks):
                row_str += pick_cell_str_table[drafter_name][round_num]
            row_str += "\n"
            pick_table_str += row_str

        pick_table_str += "```"
        # for self.pick_num, deadline in zip(range(start_pick, self.num_picks * num_drafters), pick_deadlines):
        #     round_num = self.pick_num // num_drafters
        #     drafter_idx = self.pick_num % num_drafters
        #     if round_num % 2 == 1:
        #         drafter_idx = (num_drafters - 1) - drafter_idx
        #     drafter_name = self.drafter_names[drafter_idx]
        #     time_msg_str += f"{drafter_name} - {deadline.strftime('%a. %b %d %I:%M%p')}\n" # 19 chars

        # # Start draft
        # draft_list_str = '\n'.join([f'{idx + 1}. {name}' for idx, name in enumerate(self.drafter_names)])
        # draft_order_msg = await self.draft_channel.send(f'Draft order is:\n{draft_list_str}')
        # self.current_msgs.append(draft_order_msg)

        print(time_msg_str)
        time_msg = await self.draft_channel.send(time_msg_str)
        self.current_msgs.append(time_msg)
        print(pick_table_str)
        pick_table_msg = await self.draft_channel.send(pick_table_str)
        self.current_msgs.append(pick_table_msg)

        teams_left_str = "Event Team List:\n" + "\n".join([f'{team_num:<4} - {self.team_name_dict[team_num]}' for team_num in self.all_teams])
        teams_left_msg = await self.draft_channel.send(teams_left_str)
        self.current_msgs.append(teams_left_msg)

        skip_button_view = discord.ui.View()
        skip_button_view.add_item(self.skip_button)
        skip_button_msg = await self.draft_channel.send(f"", view=skip_button_view)
        self.current_msgs.append(skip_button_msg)

        # for self.pick_num in range(start_pick, self.num_picks * num_drafters)
        self.pick_num = start_pick
        pick_idx = 0
        draft_pick_msgs = {}
        current_drafters: list[discord.Member] = []
        move_to_next_pick = True

        logger.debug(f"Starting draft at pick {pick_num}")
        # Draft Loop
        while self.pick_num < self.num_picks * num_drafters:
            round_num = self.pick_num // num_drafters
            drafter_idx = self.pick_num % num_drafters
            if round_num % 2 == 1:
                drafter_idx = (num_drafters - 1) - drafter_idx
            drafter_name = self.drafter_names[drafter_idx]
            if self.draft_picks[drafter_name][round_num] is not None:
                self.pick_num += 1
                continue

            deadline = pick_deadlines[pick_idx]
            # deadline = datetime.datetime.now() + datetime.timedelta(hours=12)

            self.current_drafter_user = discord.utils.get(self.draft_channel.members, nick=drafter_name)

            if move_to_next_pick:
                current_drafters.append(self.current_drafter_user)
            logger.debug(f"Current drafters: {current_drafters}")
            # logger.debug(f"{self.draft_channel.members=} {self.current_drafter_user.name}")
            draft_pick_msgs[self.pick_num] = await self.draft_channel.send(
                f'Current drafter is {self.current_drafter_user.mention}, deadline is **{deadline.strftime("%a. %b %d %I:%M%p")}**')

            self.current_msgs.append(draft_pick_msgs[self.pick_num])

            grid = ButtonGrid(teams_list=self.all_teams, teams_left=self.teams_left,
                              current_drafters=current_drafters, team_name_dict=self.team_name_dict)
            grid_msgs = []
            for view in grid.views:
                grid_msgs.append(await self.draft_channel.send(f"", view=view))
            self.current_msgs.extend(grid_msgs)

            # next_pick_too = ""
            # if self.pick_num % num_drafters == num_drafters - 1 and round_num != self.num_picks - 1:
            #     next_pick_too = ", you are also picking again next"

            grid.callback_futures.append(self.stop_future)
            cutoff = asyncio.create_task(self.pick_timeout(deadline))
            grid.callback_futures.append(cutoff)
            grid.callback_futures.append(self.skip_button.skip_future)

            move_to_next_pick = True

            # Wait for value from one of the team pickers' callback
            try:
                done, pending = await asyncio.wait(grid.callback_futures,
                                                   return_when=asyncio.FIRST_COMPLETED)
            except KeyboardInterrupt as e:
                logger.info(f"Interrupt signal sent, shutting down")
                # await pick_msg.delete()
                logger.debug(f"Message deleted")
                sys.exit()

            for msg in self.reminder_msgs:  # Delete pick deadline reminder messages
                await msg.delete()

            current_drafter_picked = False

            logger.debug(f"Keys: {draft_pick_msgs.keys()}")

            if self.stop_future in done:
                print("Stopping draft")
                logger.info("Stopping draft")

                # TODO Add cleanup function
                await self.cleanup_messages()
                cutoff.cancel()
                # await pick_msg.delete()
                # await draft_order_msg.delete()
                # await teams_left_msg.delete()
                # await current_msg.delete()
                return
            elif self.skip_button.skip_future in done:
                logger.info(f"Skipping {drafter_name}")
                # await self.draft_channel.send(f"Skipping {drafter_name} and allowing next drafter to start but you may still pick")
                cutoff.cancel()
                self.skip_button.reset_future()

                await draft_pick_msgs[self.pick_num].edit(
                    content=f'{self.current_drafter_user.nick}\'s #{round_num + 1} pick was skipped but may still pick')

                self.pick_num += 1
                move_to_next_pick = True

            # If automatic cutoff happened
            elif cutoff in done:
                # await self.current_drafter_user.send("Time is up!", delete_after=60)
                # await pick_msg.edit(
                #     content=f"Automatically picked team **{picked_team_num}** for {self.event_page.title} round #{round_num + 1}",
                #     view=None)
                logger.info(f"Timeout skipping {self.current_drafter_user.nick}")
                await self.draft_channel.send(f"Time is up, {self.current_drafter_user.nick}! Allowing next drafter to pick", delete_after=300)

                self.skip_button.reset_future()

                await draft_pick_msgs[self.pick_num].edit(
                    content=f'{self.current_drafter_user.nick}\'s #{round_num + 1} pick was skipped but may still pick')
                self.pick_num += 1
                move_to_next_pick = True


            else:
                # Team has been picked by either current or skipped drafter
                user_picked: discord.Member
                picked_team_num, user_picked = list(done)[0].result()
                cutoff.cancel()
                logger.debug(f"Current drafters: {current_drafters}, picker {user_picked}")
                current_drafters.remove(user_picked)

                # for msg in grid_msgs:
                #     self.current_msgs.remove(msg)  # Remove grid messages from stack
                # self.current_msgs.r
                # self.current_msgs.pop()  # Remove previous drafter message from stack

                draft_pick_round_idx = self.draft_picks[user_picked.nick].index(None) # TODO May be unnecessarily complex for common case of current drafter picking
                if user_picked == self.current_drafter_user and draft_pick_round_idx == round_num:  # Current drafter
                    draft_pick_drafter_idx = drafter_idx
                    draft_pick_num = self.pick_num
                    move_to_next_pick = True
                    logger.debug(f"Current drafter picked {draft_pick_drafter_idx} {draft_pick_num} {draft_pick_round_idx}")
                else:  # Skipped drafter
                    # round_num = self.pick_num // num_drafters
                    # drafter_idx = self.pick_num % num_drafters
                    # if round_num % 2 == 1:
                    #     drafter_idx = (num_drafters - 1) - drafter_idx

                    draft_pick_drafter_idx = self.drafter_names.index(user_picked.nick)
                    draft_pick_drafter_idx_pick_num = draft_pick_drafter_idx
                    if draft_pick_round_idx % 2 == 1:
                        draft_pick_drafter_idx_pick_num = (num_drafters - 1) - draft_pick_drafter_idx_pick_num
                    draft_pick_num = draft_pick_round_idx * num_drafters + draft_pick_drafter_idx_pick_num  # TODO Maybe could be replaced/removed
                    move_to_next_pick = False
                    logger.debug(f"Skipped drafter picked {draft_pick_drafter_idx} {draft_pick_num} {draft_pick_round_idx} {draft_pick_drafter_idx_pick_num}")
                    # Delete current drafter's message so that there will not be a duplicate when they get re-pinged
                    self.current_msgs.remove(draft_pick_msgs[self.pick_num])
                    await draft_pick_msgs[self.pick_num].delete()

                self.current_msgs.remove(draft_pick_msgs[draft_pick_num])
                # # Replace picker so multiple teams cannot be selected and to provide feedback of successful pick
                # await pick_msg.edit(
                #     content=f"You selected team **{picked_team_num}** for {self.event_page.title} round #{round_num + 1}",
                #     view=None)

                # TODO Maybe should be moved to be part of next drafter ping
                await draft_pick_msgs[draft_pick_num].edit(content=f"{self.current_drafter_user.nick} picked team {picked_team_num} for their #{draft_pick_round_idx + 1} pick")

                # await self.draft_channel.send(f'{self.current_drafter_user.nick} has picked team {picked_team_num}')

                # self.current_msgs.pop()  # Pick msg
                # self.current_msgs.pop()  # Remove previous drafter message from stack


                logger.debug(f"{user_picked.nick} picked {picked_team_num}")
                self.event_page.update_value((DRAFT_FIRST_ROW + draft_pick_drafter_idx, DRAFTER_COL + 1 + draft_pick_round_idx * 2),
                                             str(picked_team_num))
                self.teams_left.remove(picked_team_num)
                # teams_left_str = "Teams Left:\n" + "\n".join(
                #     [
                #         f'{team_num} - {self.team_name_dict[team_num]}' if team_num in self.teams_left else f'~~{team_num} - {self.team_name_dict[team_num]}~~ '
                #         for team_num in self.all_teams])
                # await teams_left_msg.edit(content=teams_left_str)
                self.draft_picks[user_picked.nick][draft_pick_round_idx] = picked_team_num
                pick_cell_str_table[user_picked.nick][draft_pick_round_idx] = f"|{picked_team_num:^{DATE_STRING_WIDTH}}"
                pick_table_str = f"{title_line}"
                for table_drafter_idx in range(num_drafters):
                    drafter_name = self.drafter_names[table_drafter_idx]
                    row_str = f"{drafter_name:>{max_len_name}} "
                    for round_num in range(self.num_picks):
                        row_str += pick_cell_str_table[drafter_name][round_num]
                    row_str += "\n"
                    pick_table_str += row_str
                pick_table_str += "```"
                await pick_table_msg.edit(content=pick_table_str)

            for msg in grid_msgs:
                await msg.delete()
                self.current_msgs.remove(msg)
            grid_msgs.clear()

            # TODO clean up futures

            self.skip_button.reset_future()

            if move_to_next_pick:
                pick_idx += 1
                pick_num += 1
        logger.info("Draft has finished!")
        await skip_button_msg.delete()
        await self.draft_channel.send(
            f'@everyone Draft for {self.event_page.title} has finished!\nSee completed event page below:\n{self.event_page.url}')

    async def cleanup_messages(self):
        for msg in self.current_msgs:
            await msg.delete()
        self.current_msgs.clear()

    def stop_draft(self):
        self.stop_future.set_result("Stop")

    async def pick_timeout(self, deadline):
        self.reminder_msgs = []
        reminders = 0
        while datetime.datetime.now() < deadline:
            if len(self.reminder_msgs) == 0 and datetime.datetime.now() >= deadline - datetime.timedelta(
                    minutes=120):  # First reminder, 2 hours
                reminder = await self.current_drafter_user.send(
                    f"You have 2 hours left to pick for {self.event_page.title}!")
                self.reminder_msgs.append(reminder)
                reminders += 1
            elif len(self.reminder_msgs) == 1 and datetime.datetime.now() >= deadline - datetime.timedelta(
                    minutes=30):  # Second reminder, 30 minutes
                reminder = await self.current_drafter_user.send(
                    f"You have 30 minutes left to pick for {self.event_page.title}!")
                self.reminder_msgs.append(reminder)
                reminders += 1
            await asyncio.sleep(1)
        return False


class ButtonGrid:
    def __init__(self, teams_list: list[int], teams_left: list[int], current_drafters: list[discord.Member], team_name_dict):

        # Create the view containing our dropdown
        self.callback_futures = []
        self.rows = []
        self.team_buttons = []
        self.views = []

        for i, team_index in enumerate(
                range(0, len(teams_list), 25)):  # 25 is the max number of buttons in a message on Discord
            # Adds the dropdown to our view object.
            button_row_list = []
            row_view = discord.ui.View(timeout=None)
            for team in teams_list[team_index:team_index + 25]:
                team_button = TeamButton(team, team not in teams_left, team_name_dict[team], current_drafters)
                # button_row_list.append(team_button)
                row_view.add_item(team_button)
                # self.team_buttons.append(team_button)
                self.callback_futures.append(team_button.click_team_future)
            self.views.append(row_view)
            # row = discord.ActionRow(children=button_row_list, row=i)
            # curr_dropdown = Dropdown(teams_list[team_index:team_index + 5], row=i)
            # self.rows.append(row)
            # self.callback_futures.append(curr_dropdown.pick_team_num_future)
            # self.dropdown = Dropdown(teams_left, row)

    def reset_futures(
            self):  # TODO Utilize to allow for reuse of button grid rather than regenerating a new one each time
        self.callback_futures = []
        for button in self.team_buttons:
            button.reset_future()
            self.callback_futures.append(button.click_team_future)


class SkipButton(discord.ui.Button):
    def __init__(self, draft: EventDraft):  # TODO Rework dependency on current user
        super(SkipButton, self).__init__(style=discord.ButtonStyle.danger, label=f"Skip Current Drafter")
        self.skip_future = asyncio.get_event_loop().create_future()
        self.draft = draft

    async def callback(self, interaction: discord.Interaction):

        # TODO Should this also allow current user to skip themselves too?
        if ADMIN_ROLE_NAME not in [r.name for r in interaction.user.roles]:
            await interaction.response.send_message(f"You must be a bot admin in order to skip someone!", ephemeral=True)
            return

        logger.info(f"Skipping current drafter")
        await interaction.response.send_message(f"Skipping {self.draft.current_drafter_user.nick} and allowing next drafter to pick", delete_after=300)
        self.skip_future.set_result(True)

    def reset_future(self):
        del self.skip_future
        self.skip_future = asyncio.get_event_loop().create_future()


class TeamButton(discord.ui.Button):

    def __init__(self, team_num, picked, team_name,
                 current_users: list[discord.Member]):  # TODO Rework dependency on current user
        label = f"{team_num:>4}"
        # label = label.replace(" "," ")
        super(TeamButton, self).__init__(style=discord.ButtonStyle.red if picked else discord.ButtonStyle.green,
                                         label=label, disabled=picked)
        # \n{team_name}
        self.team_num = team_num
        self.picked = picked
        self.current_users = current_users

        self.click_team_future = asyncio.get_event_loop().create_future()

    async def callback(self, interaction: discord.Interaction):
        if interaction.user not in self.current_users:
            return await interaction.response.send_message(f"It is not your turn to pick yet!", ephemeral=True)

        # await interaction.delete_original_response()
        self.picked = True
        logger.debug(f"Picked {self.team_num}")
        self.click_team_future.set_result((self.team_num, interaction.user))

    def reset_future(self):
        del self.click_team_future
        self.click_team_future = asyncio.get_event_loop().create_future()


class DropdownView(discord.ui.View):
    def __init__(self, teams_left: list[int], team_names: list[str]):
        super().__init__(timeout=None)

        # Create the view containing our dropdown
        self.callback_futures = []

        for i, team_index in enumerate(
                range(0, len(teams_left),
                      MAX_DISCORD_SELECTORS)):  # 25 is the max number of options in a dropdown on Discord
            # Adds the dropdown to our view object.
            curr_dropdown = Dropdown(teams_left[team_index:team_index + MAX_DISCORD_SELECTORS],
                                     team_names[team_index:team_index + MAX_DISCORD_SELECTORS], row=i)
            self.add_item(curr_dropdown)
            self.callback_futures.append(curr_dropdown.pick_team_num_future)
            # self.dropdown = Dropdown(teams_left, row)


class Dropdown(discord.ui.Select):
    def __init__(self, team_list: list[int], team_names: list[str], row=0):
        self.pick_team_num_future = asyncio.get_event_loop().create_future()
        # Set the options that will be presented inside the dropdown
        options = [discord.SelectOption(label=f"{team_num} - {team_name}") for team_num, team_name in
                   zip(team_list, team_names)]
        options = options[:MAX_DISCORD_SELECTORS]

        super().__init__(placeholder=f'Select your next pick: ({team_list[0]} - {team_list[-1]})', min_values=1,
                         max_values=1, options=options, row=row)

    async def callback(self, interaction: discord.Interaction):
        team_num = int(self.values[0].split("-")[0].strip())

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


def read_active_hour_times():
    active_hours_start_time = master_sheet.get_value(
        ACTIVE_HOURS_START_TIME_CELL)
    active_hours_end_time = master_sheet.get_value(
        ACTIVE_HOURS_END_TIME_CELL)


parser = argparse.ArgumentParser()
parser.add_argument("-d", "--debug", action='store_true')
args = parser.parse_args()


# TODO Load from config file instead
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
logger.addHandler(logging.StreamHandler(sys.stdout))

client = pygsheets.authorize(service_account_file="keys/fantasy-first-test-372522-4d15a60bbdcb.json")
sheet = client.open(sheet_name)
all_pages = sheet.worksheets()
master_sheet: pygsheets.Worksheet = sheet.worksheet("title", "Master Score Sheet")
excluded_pages = {"Master Score Sheet", "Event Template", "Old Old Event Template", "NE Top 16 Predictions", "Rules",
                  "Draft Order Roll", "Old [2022] Event Template"}
# event_ids = {'2023nhgrs', '2023mabr', '2023rinsc', '2023ctwat', '2023marea', ''}

event_pages = filter(lambda page: page.title not in excluded_pages, all_pages)
event_map: dict[str, pygsheets.Worksheet] = {event_page.cell(EVENT_ID_CELL).value: event_page for event_page in
                                             event_pages}
# print(event_map.keys())
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
        draft = EventDraft(event_id, event_page, interaction)
    except LookupError as err:
        await interaction.response.send_message(str(err), ephemeral=True)
        return

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
