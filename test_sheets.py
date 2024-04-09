import pygsheets
import numpy as np

num_drafters = 7

drafter_col = 10
draft_top_row = 5

teams_col = 2
teams_top_row = 4
teams_bot_row = 100
EVENT_ID_CELL = 'C2'

client = pygsheets.authorize(service_account_file="keys/fantasy-first-test.json.json")

sheet = client.open('2023 FF')
all_pages = sheet.worksheets()
excluded_pages = {"Master Score Sheet", "Event Template", "Old Event Template", "NE Top 16 Predictions"}
# event_ids = {'2023nhgrs', '2023mabr', '2023rinsc', '2023ctwat', '2023marea', ''}
event_pages = filter(lambda page: page.title not in excluded_pages, all_pages)
event_map: dict[str, pygsheets.Worksheet] = {event_page.cell(EVENT_ID_CELL).value: event_page for event_page in event_pages}
# for (event_id, sheet) in event_map.items():
#     print(event_id, sheet.title)

event_id = '2023nhgrs'
event_page = event_map[event_id]

teams_left = event_page.get_values((teams_top_row, teams_col), (teams_bot_row, teams_col))
teams_left = [int(team[0]) for team in teams_left]
print(f"{teams_left} {len(teams_left)}")

drafter_names = event_page.get_values((draft_top_row, drafter_col), (draft_top_row + num_drafters, drafter_col))
drafter_names = [names[0] for names in drafter_names]
print(drafter_names)

draft_idx = 0
pick_num = 1
num_picks = 3
# for pick_num in range(num_picks):
#     if pick_num % 2 == 1:
#         draft_order = reversed(range(num_drafters))
#     else:
#         draft_order = range(num_drafters)
#
#     for drafter_idx in draft_order:
#         drafter_name = drafter_names[drafter_idx]
#
#         pick = np.random.choice(teams_left)
#         teams_left.remove(pick)
#         print(f"{drafter_name} picks {pick}")
#         event_page.update_value((draft_top_row + drafter_idx, drafter_col + 1 + pick_num * 2), str(pick))

print(f'{teams_left} {len(teams_left)}')

