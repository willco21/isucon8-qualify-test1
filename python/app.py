import MySQLdb.cursors
import flask
import functools
import os
import pathlib
import copy
import json
import subprocess
from io import StringIO
import csv
from datetime import datetime, timezone
import uuid


base_path = pathlib.Path(__file__).resolve().parent.parent
static_folder = base_path / 'static'
icons_folder = base_path / 'public' / 'icons'


class CustomFlask(flask.Flask):
    jinja_options = flask.Flask.jinja_options.copy()
    jinja_options.update(dict(
        block_start_string='(%',
        block_end_string='%)',
        variable_start_string='((',
        variable_end_string='))',
        comment_start_string='(#',
        comment_end_string='#)',
    ))


app = CustomFlask(__name__, static_folder=str(static_folder), static_url_path='')
app.config['SECRET_KEY'] = 'tagomoris'


if not os.path.exists(str(icons_folder)):
    os.makedirs(str(icons_folder))


def make_base_url(request):
    return request.url_root[:-1]


@app.template_filter('tojsonsafe')
def tojsonsafe(target):
    return json.dumps(target).replace("+", "\\u002b").replace("<", "\\u003c").replace(">", "\\u003e")


def jsonify(target):
    return json.dumps(target)


def res_error(error="unknown", status=500):
    return (jsonify({"error": error}), status)


def login_required(f):
    @functools.wraps(f)
    def wrapper(*args, **kwargs):
        if not get_login_user():
            return res_error('login_required', 401)
        return f(*args, **kwargs)
    return wrapper


def admin_login_required(f):
    @functools.wraps(f)
    def wrapper(*args, **kwargs):
        if not get_login_administrator():
            return res_error('admin_login_required', 401)
        return f(*args, **kwargs)
    return wrapper


def dbh():
    if hasattr(flask.g, 'db'):
        return flask.g.db
    flask.g.db = MySQLdb.connect(
        host=os.environ['DB_HOST'],
        port=3306,
        user=os.environ['DB_USER'],
        database=os.environ['DB_DATABASE'],
        charset='utf8mb4',
        cursorclass=MySQLdb.cursors.DictCursor,
        autocommit=True,
    )
    cur = flask.g.db.cursor()
    cur.execute("SET SESSION sql_mode='STRICT_TRANS_TABLES,NO_ZERO_IN_DATE,NO_ZERO_DATE,ERROR_FOR_DIVISION_BY_ZERO,NO_ENGINE_SUBSTITUTION'")
    return flask.g.db


@app.teardown_appcontext
def teardown(error):
    if hasattr(flask.g, "db"):
        flask.g.db.close()


def get_events(filter=lambda e: True, public_fg=False):
    conn = dbh()
    conn.autocommit(False)

    cur = conn.cursor()
    try:
        cur.execute("SELECT * FROM events ORDER BY id ASC")
        rows = cur.fetchall()
        events = [row for row in rows if filter(row)]
        if public_fg:
            cur.execute("""
            select count(*) as res_count, r.event_id, s.rank
            from reservations r
            join sheets s on r.sheet_id = s.id
            join events e on r.event_id = e.id
            where r.canceled_at IS NULL AND e.public_fg = 1
            GROUP BY r.event_id, s.`rank`;
            """)
        else:
            cur.execute("""
            select count(*) as res_count, r.event_id, s.rank
            from reservations r
            join sheets s on r.sheet_id = s.id
            where r.canceled_at IS NULL
            GROUP BY r.event_id, s.`rank`;
            """)
        res_counts = cur.fetchall()
        res_counts_by_event_id_and_rank = {}

        for res_count in res_counts:
          res_counts_by_event_id_and_rank[(res_count["event_id"], res_count["rank"])] = res_count["res_count"]

        for event in events:
          s_reserve = res_counts_by_event_id_and_rank[(event["id"], "S")] if (event["id"], "S") in res_counts_by_event_id_and_rank else 0
          a_reserve = res_counts_by_event_id_and_rank[(event["id"], "A")] if (event["id"], "A") in res_counts_by_event_id_and_rank else 0
          b_reserve = res_counts_by_event_id_and_rank[(event["id"], "B")] if (event["id"], "B") in res_counts_by_event_id_and_rank else 0
          c_reserve = res_counts_by_event_id_and_rank[(event["id"], "C")] if (event["id"], "C") in res_counts_by_event_id_and_rank else 0
          event["total"] = 1000
          event["remains"] = 1000 - (s_reserve + a_reserve + b_reserve + c_reserve)
          event["sheets"] = {}
          event["sheets"] = {
          "S": {'total': 50, 'remains': 50 -s_reserve,  'price': event['price'] + 5000},
          "A": {'total': 150, 'remains': 150 - a_reserve, 'price': event['price'] + 3000},
          "B": {'total': 300, 'remains': 300 - b_reserve,  'price': event['price'] + 1000},
          "C": {'total': 500, 'remains': 500 - c_reserve,  'price': event['price']},
          }
          event['public'] = True if event['public_fg'] else False
          event['closed'] = True if event['closed_fg'] else False
          del event['public_fg']
          del event['closed_fg']


        #events = []
        #for event_id in event_ids:
        #    event = get_event(event_id)
        #    for sheet in event['sheets'].values():
        #        del sheet['detail']
        #    events.append(event)
        #conn.commit()
    except MySQLdb.Error as e:
        conn.rollback()
        raise e
    #print("return events:")
    #print(events)
    return events


def get_event(event_id, login_user_id=None):
    cur = dbh().cursor()
    cur.execute("SELECT * FROM events WHERE id = %s", [event_id])
    event = cur.fetchone()
    #print("initial event:")
    #print(event)
    if not event: return None

    event["total"] = 1000
    event["remains"] = 0
    event["sheets"] = {}
    event["sheets"] = {
        "S": {'total': 50, 'remains': 0, 'detail': [], 'price': event['price'] + 5000},
        "A": {'total': 150, 'remains': 0, 'detail': [], 'price': event['price'] + 3000},
        "B": {'total': 300, 'remains': 0, 'detail': [], 'price': event['price'] + 1000},
        "C": {'total': 500, 'remains': 0, 'detail': [], 'price': event['price']},
        }
    #for rank in ["S", "A", "B", "C"]:
    #    event["sheets"][rank] = {'total': 0, 'remains': 0, 'detail': []}

    #cur.execute("SELECT * FROM sheets ORDER BY `rank`, num")
    # event.id, event.title, event.price, event.public_fg, event.closed_fg, (event.total), (event.remains), sheets.num, (sheet.total), (sheet.remain), (sheet.reserved), (sheet.mine), reservation.reserved_at
    cur.execute("""
    SELECT *
    FROM sheets s
    join reservations r on s.id = r.sheet_id
    where
      r.event_id = %s
      AND r.canceled_at IS NULL
    """,[event["id"]])
    temp_reservations = cur.fetchall()
    reservations = {}

    for r in temp_reservations:
      reservations[r["sheet_id"]] = r

    #cur.execute("SELECT * FROM sheets ORDER BY `rank`, #num")
    #sheets = cur.fetchall()
    #print('hogehoge')
    #print(sheets)

    sheets = ({'id': 51, 'rank': 'A', 'num': 1, 'price': 3000}, {'id': 52, 'rank': 'A', 'num': 2, 'price': 3000}, {'id': 53, 'rank': 'A', 'num': 3, 'price': 3000}, {'id': 54, 'rank': 'A', 'num': 4, 'price': 3000}, {'id': 55, 'rank': 'A', 'num': 5, 'price': 3000}, {'id': 56, 'rank': 'A', 'num': 6, 'price': 3000}, {'id': 57, 'rank': 'A', 'num': 7, 'price': 3000}, {'id': 58, 'rank': 'A', 'num': 8, 'price': 3000}, {'id': 59, 'rank': 'A', 'num': 9, 'price': 3000}, {'id': 60, 'rank': 'A', 'num': 10, 'price': 3000}, {'id': 61, 'rank': 'A', 'num': 11, 'price': 3000}, {'id': 62, 'rank': 'A', 'num': 12, 'price': 3000}, {'id': 63, 'rank': 'A', 'num': 13, 'price': 3000}, {'id': 64, 'rank': 'A', 'num': 14, 'price': 3000}, {'id': 65, 'rank': 'A', 'num': 15, 'price': 3000}, {'id': 66, 'rank': 'A', 'num': 16, 'price': 3000}, {'id': 67, 'rank': 'A', 'num': 17, 'price': 3000}, {'id': 68, 'rank': 'A', 'num': 18, 'price': 3000}, {'id': 69, 'rank': 'A', 'num': 19, 'price': 3000}, {'id': 70, 'rank': 'A', 'num': 20, 'price': 3000}, {'id': 71, 'rank': 'A', 'num': 21, 'price': 3000}, {'id': 72, 'rank': 'A', 'num': 22, 'price': 3000}, {'id': 73, 'rank': 'A', 'num': 23, 'price': 3000}, {'id': 74, 'rank': 'A', 'num': 24, 'price': 3000}, {'id': 75, 'rank': 'A', 'num': 25, 'price': 3000}, {'id': 76, 'rank': 'A', 'num': 26, 'price': 3000}, {'id': 77, 'rank': 'A', 'num': 27, 'price': 3000}, {'id': 78, 'rank': 'A', 'num': 28, 'price': 3000}, {'id': 79, 'rank': 'A', 'num': 29, 'price': 3000}, {'id': 80, 'rank': 'A', 'num': 30, 'price': 3000}, {'id': 81, 'rank': 'A', 'num': 31, 'price': 3000}, {'id': 82, 'rank': 'A', 'num': 32, 'price': 3000}, {'id': 83, 'rank': 'A', 'num': 33, 'price': 3000}, {'id': 84, 'rank': 'A', 'num': 34, 'price': 3000}, {'id': 85, 'rank': 'A', 'num': 35, 'price': 3000}, {'id': 86, 'rank': 'A', 'num': 36, 'price': 3000}, {'id': 87, 'rank': 'A', 'num': 37, 'price': 3000}, {'id': 88, 'rank': 'A', 'num': 38, 'price': 3000}, {'id': 89, 'rank': 'A', 'num': 39, 'price': 3000}, {'id': 90, 'rank': 'A', 'num': 40, 'price': 3000}, {'id': 91, 'rank': 'A', 'num': 41, 'price': 3000}, {'id': 92, 'rank': 'A', 'num': 42, 'price': 3000}, {'id': 93, 'rank': 'A', 'num': 43, 'price': 3000}, {'id': 94, 'rank': 'A', 'num': 44, 'price': 3000}, {'id': 95, 'rank': 'A', 'num': 45, 'price': 3000}, {'id': 96, 'rank': 'A', 'num': 46, 'price': 3000}, {'id': 97, 'rank': 'A', 'num': 47, 'price': 3000}, {'id': 98, 'rank': 'A', 'num': 48, 'price': 3000}, {'id': 99, 'rank': 'A', 'num': 49, 'price': 3000}, {'id': 100, 'rank': 'A', 'num': 50, 'price': 3000}, {'id': 101, 'rank': 'A', 'num': 51, 'price': 3000}, {'id': 102, 'rank': 'A', 'num': 52, 'price': 3000}, {'id': 103, 'rank': 'A', 'num': 53, 'price': 3000}, {'id': 104, 'rank': 'A', 'num': 54, 'price': 3000}, {'id': 105, 'rank': 'A', 'num': 55, 'price': 3000}, {'id': 106, 'rank': 'A', 'num': 56, 'price': 3000}, {'id': 107, 'rank': 'A', 'num': 57, 'price': 3000}, {'id': 108, 'rank': 'A', 'num': 58, 'price': 3000}, {'id': 109, 'rank': 'A', 'num': 59, 'price': 3000}, {'id': 110, 'rank': 'A', 'num': 60, 'price': 3000}, {'id': 111, 'rank': 'A', 'num': 61, 'price': 3000}, {'id': 112, 'rank': 'A', 'num': 62, 'price': 3000}, {'id': 113, 'rank': 'A', 'num': 63, 'price': 3000}, {'id': 114, 'rank': 'A', 'num': 64, 'price': 3000}, {'id': 115, 'rank': 'A', 'num': 65, 'price': 3000}, {'id': 116, 'rank': 'A', 'num': 66, 'price': 3000}, {'id': 117, 'rank': 'A', 'num': 67, 'price': 3000}, {'id': 118, 'rank': 'A', 'num': 68, 'price': 3000}, {'id': 119, 'rank': 'A', 'num': 69, 'price': 3000}, {'id': 120, 'rank': 'A', 'num': 70, 'price': 3000}, {'id': 121, 'rank': 'A', 'num': 71, 'price': 3000}, {'id': 122, 'rank': 'A', 'num': 72, 'price': 3000}, {'id': 123, 'rank': 'A', 'num': 73, 'price': 3000}, {'id': 124, 'rank': 'A', 'num': 74, 'price': 3000}, {'id': 125, 'rank': 'A', 'num': 75, 'price': 3000}, {'id': 126, 'rank': 'A', 'num': 76, 'price': 3000}, {'id': 127, 'rank': 'A', 'num': 77, 'price': 3000}, {'id': 128, 'rank': 'A', 'num': 78, 'price': 3000}, {'id': 129, 'rank': 'A', 'num': 79, 'price': 3000}, {'id': 130, 'rank': 'A', 'num': 80, 'price': 3000}, {'id': 131, 'rank': 'A', 'num': 81, 'price': 3000}, {'id': 132, 'rank': 'A', 'num': 82, 'price': 3000}, {'id': 133, 'rank': 'A', 'num': 83, 'price': 3000}, {'id': 134, 'rank': 'A', 'num': 84, 'price': 3000}, {'id': 135, 'rank': 'A', 'num': 85, 'price': 3000}, {'id': 136, 'rank': 'A', 'num': 86, 'price': 3000}, {'id': 137, 'rank': 'A', 'num': 87, 'price': 3000}, {'id': 138, 'rank': 'A', 'num': 88, 'price': 3000}, {'id': 139, 'rank': 'A', 'num': 89, 'price': 3000}, {'id': 140, 'rank': 'A', 'num': 90, 'price': 3000}, {'id': 141, 'rank': 'A', 'num': 91, 'price': 3000}, {'id': 142, 'rank': 'A', 'num': 92, 'price': 3000}, {'id': 143, 'rank': 'A', 'num': 93, 'price': 3000}, {'id': 144, 'rank': 'A', 'num': 94, 'price': 3000}, {'id': 145, 'rank': 'A', 'num': 95, 'price': 3000}, {'id': 146, 'rank': 'A', 'num': 96, 'price': 3000}, {'id': 147, 'rank': 'A', 'num': 97, 'price': 3000}, {'id': 148, 'rank': 'A', 'num': 98, 'price': 3000}, {'id': 149, 'rank': 'A', 'num': 99, 'price': 3000}, {'id': 150, 'rank': 'A', 'num': 100, 'price': 3000}, {'id': 151, 'rank': 'A', 'num': 101, 'price': 3000}, {'id': 152, 'rank': 'A', 'num': 102, 'price': 3000}, {'id': 153, 'rank': 'A', 'num': 103, 'price': 3000}, {'id': 154, 'rank': 'A', 'num': 104, 'price': 3000}, {'id': 155, 'rank': 'A', 'num': 105, 'price': 3000}, {'id': 156, 'rank': 'A', 'num': 106, 'price': 3000}, {'id': 157, 'rank': 'A', 'num': 107, 'price': 3000}, {'id': 158, 'rank': 'A', 'num': 108, 'price': 3000}, {'id': 159, 'rank': 'A', 'num': 109, 'price': 3000}, {'id': 160, 'rank': 'A', 'num': 110, 'price': 3000}, {'id': 161, 'rank': 'A', 'num': 111, 'price': 3000}, {'id': 162, 'rank': 'A', 'num': 112, 'price': 3000}, {'id': 163, 'rank': 'A', 'num': 113, 'price': 3000}, {'id': 164, 'rank': 'A', 'num': 114, 'price': 3000}, {'id': 165, 'rank': 'A', 'num': 115, 'price': 3000}, {'id': 166, 'rank': 'A', 'num': 116, 'price': 3000}, {'id': 167, 'rank': 'A', 'num': 117, 'price': 3000}, {'id': 168, 'rank': 'A', 'num': 118, 'price': 3000}, {'id': 169, 'rank': 'A', 'num': 119, 'price': 3000}, {'id': 170, 'rank': 'A', 'num': 120, 'price': 3000}, {'id': 171, 'rank': 'A', 'num': 121, 'price': 3000}, {'id': 172, 'rank': 'A', 'num': 122, 'price': 3000}, {'id': 173, 'rank': 'A', 'num': 123, 'price': 3000}, {'id': 174, 'rank': 'A', 'num': 124, 'price': 3000}, {'id': 175, 'rank': 'A', 'num': 125, 'price': 3000}, {'id': 176, 'rank': 'A', 'num': 126, 'price': 3000}, {'id': 177, 'rank': 'A', 'num': 127, 'price': 3000}, {'id': 178, 'rank': 'A', 'num': 128, 'price': 3000}, {'id': 179, 'rank': 'A', 'num': 129, 'price': 3000}, {'id': 180, 'rank': 'A', 'num': 130, 'price': 3000}, {'id': 181, 'rank': 'A', 'num': 131, 'price': 3000}, {'id': 182, 'rank': 'A', 'num': 132, 'price': 3000}, {'id': 183, 'rank': 'A', 'num': 133, 'price': 3000}, {'id': 184, 'rank': 'A', 'num': 134, 'price': 3000}, {'id': 185, 'rank': 'A', 'num': 135, 'price': 3000}, {'id': 186, 'rank': 'A', 'num': 136, 'price': 3000}, {'id': 187, 'rank': 'A', 'num': 137, 'price': 3000}, {'id': 188, 'rank': 'A', 'num': 138, 'price': 3000}, {'id': 189, 'rank': 'A', 'num': 139, 'price': 3000}, {'id': 190, 'rank': 'A', 'num': 140, 'price': 3000}, {'id': 191, 'rank': 'A', 'num': 141, 'price': 3000}, {'id': 192, 'rank': 'A', 'num': 142, 'price': 3000}, {'id': 193, 'rank': 'A', 'num': 143, 'price': 3000}, {'id': 194, 'rank': 'A', 'num': 144, 'price': 3000}, {'id': 195, 'rank': 'A', 'num': 145, 'price': 3000}, {'id': 196, 'rank': 'A', 'num': 146, 'price': 3000}, {'id': 197, 'rank': 'A', 'num': 147, 'price': 3000}, {'id': 198, 'rank': 'A', 'num': 148, 'price': 3000}, {'id': 199, 'rank': 'A', 'num': 149, 'price': 3000}, {'id': 200, 'rank': 'A', 'num': 150, 'price': 3000}, {'id': 201, 'rank': 'B', 'num': 1, 'price': 1000}, {'id': 202, 'rank': 'B', 'num': 2, 'price': 1000}, {'id': 203, 'rank': 'B', 'num': 3, 'price': 1000}, {'id': 204, 'rank': 'B', 'num': 4, 'price': 1000}, {'id': 205, 'rank': 'B', 'num': 5, 'price': 1000}, {'id': 206, 'rank': 'B', 'num': 6, 'price': 1000}, {'id': 207, 'rank': 'B', 'num': 7, 'price': 1000}, {'id': 208, 'rank': 'B', 'num': 8, 'price': 1000}, {'id': 209, 'rank': 'B', 'num': 9, 'price': 1000}, {'id': 210, 'rank': 'B', 'num': 10, 'price': 1000}, {'id': 211, 'rank': 'B', 'num': 11, 'price': 1000}, {'id': 212, 'rank': 'B', 'num': 12, 'price': 1000}, {'id': 213, 'rank': 'B', 'num': 13, 'price': 1000}, {'id': 214, 'rank': 'B', 'num': 14, 'price': 1000}, {'id': 215, 'rank': 'B', 'num': 15, 'price': 1000}, {'id': 216, 'rank': 'B', 'num': 16, 'price': 1000}, {'id': 217, 'rank': 'B', 'num': 17, 'price': 1000}, {'id': 218, 'rank': 'B', 'num': 18, 'price': 1000}, {'id': 219, 'rank': 'B', 'num': 19, 'price': 1000}, {'id': 220, 'rank': 'B', 'num': 20, 'price': 1000}, {'id': 221, 'rank': 'B', 'num': 21, 'price': 1000}, {'id': 222, 'rank': 'B', 'num': 22, 'price': 1000}, {'id': 223, 'rank': 'B', 'num': 23, 'price': 1000}, {'id': 224, 'rank': 'B', 'num': 24, 'price': 1000}, {'id': 225, 'rank': 'B', 'num': 25, 'price': 1000}, {'id': 226, 'rank': 'B', 'num': 26, 'price': 1000}, {'id': 227, 'rank': 'B', 'num': 27, 'price': 1000}, {'id': 228, 'rank': 'B', 'num': 28, 'price': 1000}, {'id': 229, 'rank': 'B', 'num': 29, 'price': 1000}, {'id': 230, 'rank': 'B', 'num': 30, 'price': 1000}, {'id': 231, 'rank': 'B', 'num': 31, 'price': 1000}, {'id': 232, 'rank': 'B', 'num': 32, 'price': 1000}, {'id': 233, 'rank': 'B', 'num': 33, 'price': 1000}, {'id': 234, 'rank': 'B', 'num': 34, 'price': 1000}, {'id': 235, 'rank': 'B', 'num': 35, 'price': 1000}, {'id': 236, 'rank': 'B', 'num': 36, 'price': 1000}, {'id': 237, 'rank': 'B', 'num': 37, 'price': 1000}, {'id': 238, 'rank': 'B', 'num': 38, 'price': 1000}, {'id': 239, 'rank': 'B', 'num': 39, 'price': 1000}, {'id': 240, 'rank': 'B', 'num': 40, 'price': 1000}, {'id': 241, 'rank': 'B', 'num': 41, 'price': 1000}, {'id': 242, 'rank': 'B', 'num': 42, 'price': 1000}, {'id': 243, 'rank': 'B', 'num': 43, 'price': 1000}, {'id': 244, 'rank': 'B', 'num': 44, 'price': 1000}, {'id': 245, 'rank': 'B', 'num': 45, 'price': 1000}, {'id': 246, 'rank': 'B', 'num': 46, 'price': 1000}, {'id': 247, 'rank': 'B', 'num': 47, 'price': 1000}, {'id': 248, 'rank': 'B', 'num': 48, 'price': 1000}, {'id': 249, 'rank': 'B', 'num': 49, 'price': 1000}, {'id': 250, 'rank': 'B', 'num': 50, 'price': 1000}, {'id': 251, 'rank': 'B', 'num': 51, 'price': 1000}, {'id': 252, 'rank': 'B', 'num': 52, 'price': 1000}, {'id': 253, 'rank': 'B', 'num': 53, 'price': 1000}, {'id': 254, 'rank': 'B', 'num': 54, 'price': 1000}, {'id': 255, 'rank': 'B', 'num': 55, 'price': 1000}, {'id': 256, 'rank': 'B', 'num': 56, 'price': 1000}, {'id': 257, 'rank': 'B', 'num': 57, 'price': 1000}, {'id': 258, 'rank': 'B', 'num': 58, 'price': 1000}, {'id': 259, 'rank': 'B', 'num': 59, 'price': 1000}, {'id': 260, 'rank': 'B', 'num': 60, 'price': 1000}, {'id': 261, 'rank': 'B', 'num': 61, 'price': 1000}, {'id': 262, 'rank': 'B', 'num': 62, 'price': 1000}, {'id': 263, 'rank': 'B', 'num': 63, 'price': 1000}, {'id': 264, 'rank': 'B', 'num': 64, 'price': 1000}, {'id': 265, 'rank': 'B', 'num': 65, 'price': 1000}, {'id': 266, 'rank': 'B', 'num': 66, 'price': 1000}, {'id': 267, 'rank': 'B', 'num': 67, 'price': 1000}, {'id': 268, 'rank': 'B', 'num': 68, 'price': 1000}, {'id': 269, 'rank': 'B', 'num': 69, 'price': 1000}, {'id': 270, 'rank': 'B', 'num': 70, 'price': 1000}, {'id': 271, 'rank': 'B', 'num': 71, 'price': 1000}, {'id': 272, 'rank': 'B', 'num': 72, 'price': 1000}, {'id': 273, 'rank': 'B', 'num': 73, 'price': 1000}, {'id': 274, 'rank': 'B', 'num': 74, 'price': 1000}, {'id': 275, 'rank': 'B', 'num': 75, 'price': 1000}, {'id': 276, 'rank': 'B', 'num': 76, 'price': 1000}, {'id': 277, 'rank': 'B', 'num': 77, 'price': 1000}, {'id': 278, 'rank': 'B', 'num': 78, 'price': 1000}, {'id': 279, 'rank': 'B', 'num': 79, 'price': 1000}, {'id': 280, 'rank': 'B', 'num': 80, 'price': 1000}, {'id': 281, 'rank': 'B', 'num': 81, 'price': 1000}, {'id': 282, 'rank': 'B', 'num': 82, 'price': 1000}, {'id': 283, 'rank': 'B', 'num': 83, 'price': 1000}, {'id': 284, 'rank': 'B', 'num': 84, 'price': 1000}, {'id': 285, 'rank': 'B', 'num': 85, 'price': 1000}, {'id': 286, 'rank': 'B', 'num': 86, 'price': 1000}, {'id': 287, 'rank': 'B', 'num': 87, 'price': 1000}, {'id': 288, 'rank': 'B', 'num': 88, 'price': 1000}, {'id': 289, 'rank': 'B', 'num': 89, 'price': 1000}, {'id': 290, 'rank': 'B', 'num': 90, 'price': 1000}, {'id': 291, 'rank': 'B', 'num': 91, 'price': 1000}, {'id': 292, 'rank': 'B', 'num': 92, 'price': 1000}, {'id': 293, 'rank': 'B', 'num': 93, 'price': 1000}, {'id': 294, 'rank': 'B', 'num': 94, 'price': 1000}, {'id': 295, 'rank': 'B', 'num': 95, 'price': 1000}, {'id': 296, 'rank': 'B', 'num': 96, 'price': 1000}, {'id': 297, 'rank': 'B', 'num': 97, 'price': 1000}, {'id': 298, 'rank': 'B', 'num': 98, 'price': 1000}, {'id': 299, 'rank': 'B', 'num': 99, 'price': 1000}, {'id': 300, 'rank': 'B', 'num': 100, 'price': 1000}, {'id': 301, 'rank': 'B', 'num': 101, 'price': 1000}, {'id': 302, 'rank': 'B', 'num': 102, 'price': 1000}, {'id': 303, 'rank': 'B', 'num': 103, 'price': 1000}, {'id': 304, 'rank': 'B', 'num': 104, 'price': 1000}, {'id': 305, 'rank': 'B', 'num': 105, 'price': 1000}, {'id': 306, 'rank': 'B', 'num': 106, 'price': 1000}, {'id': 307, 'rank': 'B', 'num': 107, 'price': 1000}, {'id': 308, 'rank': 'B', 'num': 108, 'price': 1000}, {'id': 309, 'rank': 'B', 'num': 109, 'price': 1000}, {'id': 310, 'rank': 'B', 'num': 110, 'price': 1000}, {'id': 311, 'rank': 'B', 'num': 111, 'price': 1000}, {'id': 312, 'rank': 'B', 'num': 112, 'price': 1000}, {'id': 313, 'rank': 'B', 'num': 113, 'price': 1000}, {'id': 314, 'rank': 'B', 'num': 114, 'price': 1000}, {'id': 315, 'rank': 'B', 'num': 115, 'price': 1000}, {'id': 316, 'rank': 'B', 'num': 116, 'price': 1000}, {'id': 317, 'rank': 'B', 'num': 117, 'price': 1000}, {'id': 318, 'rank': 'B', 'num': 118, 'price': 1000}, {'id': 319, 'rank': 'B', 'num': 119, 'price': 1000}, {'id': 320, 'rank': 'B', 'num': 120, 'price': 1000}, {'id': 321, 'rank': 'B', 'num': 121, 'price': 1000}, {'id': 322, 'rank': 'B', 'num': 122, 'price': 1000}, {'id': 323, 'rank': 'B', 'num': 123, 'price': 1000}, {'id': 324, 'rank': 'B', 'num': 124, 'price': 1000}, {'id': 325, 'rank': 'B', 'num': 125, 'price': 1000}, {'id': 326, 'rank': 'B', 'num': 126, 'price': 1000}, {'id': 327, 'rank': 'B', 'num': 127, 'price': 1000}, {'id': 328, 'rank': 'B', 'num': 128, 'price': 1000}, {'id': 329, 'rank': 'B', 'num': 129, 'price': 1000}, {'id': 330, 'rank': 'B', 'num': 130, 'price': 1000}, {'id': 331, 'rank': 'B', 'num': 131, 'price': 1000}, {'id': 332, 'rank': 'B', 'num': 132, 'price': 1000}, {'id': 333, 'rank': 'B', 'num': 133, 'price': 1000}, {'id': 334, 'rank': 'B', 'num': 134, 'price': 1000}, {'id': 335, 'rank': 'B', 'num': 135, 'price': 1000}, {'id': 336, 'rank': 'B', 'num': 136, 'price': 1000}, {'id': 337, 'rank': 'B', 'num': 137, 'price': 1000}, {'id': 338, 'rank': 'B', 'num': 138, 'price': 1000}, {'id': 339, 'rank': 'B', 'num': 139, 'price': 1000}, {'id': 340, 'rank': 'B', 'num': 140, 'price': 1000}, {'id': 341, 'rank': 'B', 'num': 141, 'price': 1000}, {'id': 342, 'rank': 'B', 'num': 142, 'price': 1000}, {'id': 343, 'rank': 'B', 'num': 143, 'price': 1000}, {'id': 344, 'rank': 'B', 'num': 144, 'price': 1000}, {'id': 345, 'rank': 'B', 'num': 145, 'price': 1000}, {'id': 346, 'rank': 'B', 'num': 146, 'price': 1000}, {'id': 347, 'rank': 'B', 'num': 147, 'price': 1000}, {'id': 348, 'rank': 'B', 'num': 148, 'price': 1000}, {'id': 349, 'rank': 'B', 'num': 149, 'price': 1000}, {'id': 350, 'rank': 'B', 'num': 150, 'price': 1000}, {'id': 351, 'rank': 'B', 'num': 151, 'price': 1000}, {'id': 352, 'rank': 'B', 'num': 152, 'price': 1000}, {'id': 353, 'rank': 'B', 'num': 153, 'price': 1000}, {'id': 354, 'rank': 'B', 'num': 154, 'price': 1000}, {'id': 355, 'rank': 'B', 'num': 155, 'price': 1000}, {'id': 356, 'rank': 'B', 'num': 156, 'price': 1000}, {'id': 357, 'rank': 'B', 'num': 157, 'price': 1000}, {'id': 358, 'rank': 'B', 'num': 158, 'price': 1000}, {'id': 359, 'rank': 'B', 'num': 159, 'price': 1000}, {'id': 360, 'rank': 'B', 'num': 160, 'price': 1000}, {'id': 361, 'rank': 'B', 'num': 161, 'price': 1000}, {'id': 362, 'rank': 'B', 'num': 162, 'price': 1000}, {'id': 363, 'rank': 'B', 'num': 163, 'price': 1000}, {'id': 364, 'rank': 'B', 'num': 164, 'price': 1000}, {'id': 365, 'rank': 'B', 'num': 165, 'price': 1000}, {'id': 366, 'rank': 'B', 'num': 166, 'price': 1000}, {'id': 367, 'rank': 'B', 'num': 167, 'price': 1000}, {'id': 368, 'rank': 'B', 'num': 168, 'price': 1000}, {'id': 369, 'rank': 'B', 'num': 169, 'price': 1000}, {'id': 370, 'rank': 'B', 'num': 170, 'price': 1000}, {'id': 371, 'rank': 'B', 'num': 171, 'price': 1000}, {'id': 372, 'rank': 'B', 'num': 172, 'price': 1000}, {'id': 373, 'rank': 'B', 'num': 173, 'price': 1000}, {'id': 374, 'rank': 'B', 'num': 174, 'price': 1000}, {'id': 375, 'rank': 'B', 'num': 175, 'price': 1000}, {'id': 376, 'rank': 'B', 'num': 176, 'price': 1000}, {'id': 377, 'rank': 'B', 'num': 177, 'price': 1000}, {'id': 378, 'rank': 'B', 'num': 178, 'price': 1000}, {'id': 379, 'rank': 'B', 'num': 179, 'price': 1000}, {'id': 380, 'rank': 'B', 'num': 180, 'price': 1000}, {'id': 381, 'rank': 'B', 'num': 181, 'price': 1000}, {'id': 382, 'rank': 'B', 'num': 182, 'price': 1000}, {'id': 383, 'rank': 'B', 'num': 183, 'price': 1000}, {'id': 384, 'rank': 'B', 'num': 184, 'price': 1000}, {'id': 385, 'rank': 'B', 'num': 185, 'price': 1000}, {'id': 386, 'rank': 'B', 'num': 186, 'price': 1000}, {'id': 387, 'rank': 'B', 'num': 187, 'price': 1000}, {'id': 388, 'rank': 'B', 'num': 188, 'price': 1000}, {'id': 389, 'rank': 'B', 'num': 189, 'price': 1000}, {'id': 390, 'rank': 'B', 'num': 190, 'price': 1000}, {'id': 391, 'rank': 'B', 'num': 191, 'price': 1000}, {'id': 392, 'rank': 'B', 'num': 192, 'price': 1000}, {'id': 393, 'rank': 'B', 'num': 193, 'price': 1000}, {'id': 394, 'rank': 'B', 'num': 194, 'price': 1000}, {'id': 395, 'rank': 'B', 'num': 195, 'price': 1000}, {'id': 396, 'rank': 'B', 'num': 196, 'price': 1000}, {'id': 397, 'rank': 'B', 'num': 197, 'price': 1000}, {'id': 398, 'rank': 'B', 'num': 198, 'price': 1000}, {'id': 399, 'rank': 'B', 'num': 199, 'price': 1000}, {'id': 400, 'rank': 'B', 'num': 200, 'price': 1000}, {'id': 401, 'rank': 'B', 'num': 201, 'price': 1000}, {'id': 402, 'rank': 'B', 'num': 202, 'price': 1000}, {'id': 403, 'rank': 'B', 'num': 203, 'price': 1000}, {'id': 404, 'rank': 'B', 'num': 204, 'price': 1000}, {'id': 405, 'rank': 'B', 'num': 205, 'price': 1000}, {'id': 406, 'rank': 'B', 'num': 206, 'price': 1000}, {'id': 407, 'rank': 'B', 'num': 207, 'price': 1000}, {'id': 408, 'rank': 'B', 'num': 208, 'price': 1000}, {'id': 409, 'rank': 'B', 'num': 209, 'price': 1000}, {'id': 410, 'rank': 'B', 'num': 210, 'price': 1000}, {'id': 411, 'rank': 'B', 'num': 211, 'price': 1000}, {'id': 412, 'rank': 'B', 'num': 212, 'price': 1000}, {'id': 413, 'rank': 'B', 'num': 213, 'price': 1000}, {'id': 414, 'rank': 'B', 'num': 214, 'price': 1000}, {'id': 415, 'rank': 'B', 'num': 215, 'price': 1000}, {'id': 416, 'rank': 'B', 'num': 216, 'price': 1000}, {'id': 417, 'rank': 'B', 'num': 217, 'price': 1000}, {'id': 418, 'rank': 'B', 'num': 218, 'price': 1000}, {'id': 419, 'rank': 'B', 'num': 219, 'price': 1000}, {'id': 420, 'rank': 'B', 'num': 220, 'price': 1000}, {'id': 421, 'rank': 'B', 'num': 221, 'price': 1000}, {'id': 422, 'rank': 'B', 'num': 222, 'price': 1000}, {'id': 423, 'rank': 'B', 'num': 223, 'price': 1000}, {'id': 424, 'rank': 'B', 'num': 224, 'price': 1000}, {'id': 425, 'rank': 'B', 'num': 225, 'price': 1000}, {'id': 426, 'rank': 'B', 'num': 226, 'price': 1000}, {'id': 427, 'rank': 'B', 'num': 227, 'price': 1000}, {'id': 428, 'rank': 'B', 'num': 228, 'price': 1000}, {'id': 429, 'rank': 'B', 'num': 229, 'price': 1000}, {'id': 430, 'rank': 'B', 'num': 230, 'price': 1000}, {'id': 431, 'rank': 'B', 'num': 231, 'price': 1000}, {'id': 432, 'rank': 'B', 'num': 232, 'price': 1000}, {'id': 433, 'rank': 'B', 'num': 233, 'price': 1000}, {'id': 434, 'rank': 'B', 'num': 234, 'price': 1000}, {'id': 435, 'rank': 'B', 'num': 235, 'price': 1000}, {'id': 436, 'rank': 'B', 'num': 236, 'price': 1000}, {'id': 437, 'rank': 'B', 'num': 237, 'price': 1000}, {'id': 438, 'rank': 'B', 'num': 238, 'price': 1000}, {'id': 439, 'rank': 'B', 'num': 239, 'price': 1000}, {'id': 440, 'rank': 'B', 'num': 240, 'price': 1000}, {'id': 441, 'rank': 'B', 'num': 241, 'price': 1000}, {'id': 442, 'rank': 'B', 'num': 242, 'price': 1000}, {'id': 443, 'rank': 'B', 'num': 243, 'price': 1000}, {'id': 444, 'rank': 'B', 'num': 244, 'price': 1000}, {'id': 445, 'rank': 'B', 'num': 245, 'price': 1000}, {'id': 446, 'rank': 'B', 'num': 246, 'price': 1000}, {'id': 447, 'rank': 'B', 'num': 247, 'price': 1000}, {'id': 448, 'rank': 'B', 'num': 248, 'price': 1000}, {'id': 449, 'rank': 'B', 'num': 249, 'price': 1000}, {'id': 450, 'rank': 'B', 'num': 250, 'price': 1000}, {'id': 451, 'rank': 'B', 'num': 251, 'price': 1000}, {'id': 452, 'rank': 'B', 'num': 252, 'price': 1000}, {'id': 453, 'rank': 'B', 'num': 253, 'price': 1000}, {'id': 454, 'rank': 'B', 'num': 254, 'price': 1000}, {'id': 455, 'rank': 'B', 'num': 255, 'price': 1000}, {'id': 456, 'rank': 'B', 'num': 256, 'price': 1000}, {'id': 457, 'rank': 'B', 'num': 257, 'price': 1000}, {'id': 458, 'rank': 'B', 'num': 258, 'price': 1000}, {'id': 459, 'rank': 'B', 'num': 259, 'price': 1000}, {'id': 460, 'rank': 'B', 'num': 260, 'price': 1000}, {'id': 461, 'rank': 'B', 'num': 261, 'price': 1000}, {'id': 462, 'rank': 'B', 'num': 262, 'price': 1000}, {'id': 463, 'rank': 'B', 'num': 263, 'price': 1000}, {'id': 464, 'rank': 'B', 'num': 264, 'price': 1000}, {'id': 465, 'rank': 'B', 'num': 265, 'price': 1000}, {'id': 466, 'rank': 'B', 'num': 266, 'price': 1000}, {'id': 467, 'rank': 'B', 'num': 267, 'price': 1000}, {'id': 468, 'rank': 'B', 'num': 268, 'price': 1000}, {'id': 469, 'rank': 'B', 'num': 269, 'price': 1000}, {'id': 470, 'rank': 'B', 'num': 270, 'price': 1000}, {'id': 471, 'rank': 'B', 'num': 271, 'price': 1000}, {'id': 472, 'rank': 'B', 'num': 272, 'price': 1000}, {'id': 473, 'rank': 'B', 'num': 273, 'price': 1000}, {'id': 474, 'rank': 'B', 'num': 274, 'price': 1000}, {'id': 475, 'rank': 'B', 'num': 275, 'price': 1000}, {'id': 476, 'rank': 'B', 'num': 276, 'price': 1000}, {'id': 477, 'rank': 'B', 'num': 277, 'price': 1000}, {'id': 478, 'rank': 'B', 'num': 278, 'price': 1000}, {'id': 479, 'rank': 'B', 'num': 279, 'price': 1000}, {'id': 480, 'rank': 'B', 'num': 280, 'price': 1000}, {'id': 481, 'rank': 'B', 'num': 281, 'price': 1000}, {'id': 482, 'rank': 'B', 'num': 282, 'price': 1000}, {'id': 483, 'rank': 'B', 'num': 283, 'price': 1000}, {'id': 484, 'rank': 'B', 'num': 284, 'price': 1000}, {'id': 485, 'rank': 'B', 'num': 285, 'price': 1000}, {'id': 486, 'rank': 'B', 'num': 286, 'price': 1000}, {'id': 487, 'rank': 'B', 'num': 287, 'price': 1000}, {'id': 488, 'rank': 'B', 'num': 288, 'price': 1000}, {'id': 489, 'rank': 'B', 'num': 289, 'price': 1000}, {'id': 490, 'rank': 'B', 'num': 290, 'price': 1000}, {'id': 491, 'rank': 'B', 'num': 291, 'price': 1000}, {'id': 492, 'rank': 'B', 'num': 292, 'price': 1000}, {'id': 493, 'rank': 'B', 'num': 293, 'price': 1000}, {'id': 494, 'rank': 'B', 'num': 294, 'price': 1000}, {'id': 495, 'rank': 'B', 'num': 295, 'price': 1000}, {'id': 496, 'rank': 'B', 'num': 296, 'price': 1000}, {'id': 497, 'rank': 'B', 'num': 297, 'price': 1000}, {'id': 498, 'rank': 'B', 'num': 298, 'price': 1000}, {'id': 499, 'rank': 'B', 'num': 299, 'price': 1000}, {'id': 500, 'rank': 'B', 'num': 300, 'price': 1000}, {'id': 501, 'rank': 'C', 'num': 1, 'price': 0}, {'id': 502, 'rank': 'C', 'num': 2, 'price': 0}, {'id': 503, 'rank': 'C', 'num': 3, 'price': 0}, {'id': 504, 'rank': 'C', 'num': 4, 'price': 0}, {'id': 505, 'rank': 'C', 'num': 5, 'price': 0}, {'id': 506, 'rank': 'C', 'num': 6, 'price': 0}, {'id': 507, 'rank': 'C', 'num': 7, 'price': 0}, {'id': 508, 'rank': 'C', 'num': 8, 'price': 0}, {'id': 509, 'rank': 'C', 'num': 9, 'price': 0}, {'id': 510, 'rank': 'C', 'num': 10, 'price': 0}, {'id': 511, 'rank': 'C', 'num': 11, 'price': 0}, {'id': 512, 'rank': 'C', 'num': 12, 'price': 0}, {'id': 513, 'rank': 'C', 'num': 13, 'price': 0}, {'id': 514, 'rank': 'C', 'num': 14, 'price': 0}, {'id': 515, 'rank': 'C', 'num': 15, 'price': 0}, {'id': 516, 'rank': 'C', 'num': 16, 'price': 0}, {'id': 517, 'rank': 'C', 'num': 17, 'price': 0}, {'id': 518, 'rank': 'C', 'num': 18, 'price': 0}, {'id': 519, 'rank': 'C', 'num': 19, 'price': 0}, {'id': 520, 'rank': 'C', 'num': 20, 'price': 0}, {'id': 521, 'rank': 'C', 'num': 21, 'price': 0}, {'id': 522, 'rank': 'C', 'num': 22, 'price': 0}, {'id': 523, 'rank': 'C', 'num': 23, 'price': 0}, {'id': 524, 'rank': 'C', 'num': 24, 'price': 0}, {'id': 525, 'rank': 'C', 'num': 25, 'price': 0}, {'id': 526, 'rank': 'C', 'num': 26, 'price': 0}, {'id': 527, 'rank': 'C', 'num': 27, 'price': 0}, {'id': 528, 'rank': 'C', 'num': 28, 'price': 0}, {'id': 529, 'rank': 'C', 'num': 29, 'price': 0}, {'id': 530, 'rank': 'C', 'num': 30, 'price': 0}, {'id': 531, 'rank': 'C', 'num': 31, 'price': 0}, {'id': 532, 'rank': 'C', 'num': 32, 'price': 0}, {'id': 533, 'rank': 'C', 'num': 33, 'price': 0}, {'id': 534, 'rank': 'C', 'num': 34, 'price': 0}, {'id': 535, 'rank': 'C', 'num': 35, 'price': 0}, {'id': 536, 'rank': 'C', 'num': 36, 'price': 0}, {'id': 537, 'rank': 'C', 'num': 37, 'price': 0}, {'id': 538, 'rank': 'C', 'num': 38, 'price': 0}, {'id': 539, 'rank': 'C', 'num': 39, 'price': 0}, {'id': 540, 'rank': 'C', 'num': 40, 'price': 0}, {'id': 541, 'rank': 'C', 'num': 41, 'price': 0}, {'id': 542, 'rank': 'C', 'num': 42, 'price': 0}, {'id': 543, 'rank': 'C', 'num': 43, 'price': 0}, {'id': 544, 'rank': 'C', 'num': 44, 'price': 0}, {'id': 545, 'rank': 'C', 'num': 45, 'price': 0}, {'id': 546, 'rank': 'C', 'num': 46, 'price': 0}, {'id': 547, 'rank': 'C', 'num': 47, 'price': 0}, {'id': 548, 'rank': 'C', 'num': 48, 'price': 0}, {'id': 549, 'rank': 'C', 'num': 49, 'price': 0}, {'id': 550, 'rank': 'C', 'num': 50, 'price': 0}, {'id': 551, 'rank': 'C', 'num': 51, 'price': 0}, {'id': 552, 'rank': 'C', 'num': 52, 'price': 0}, {'id': 553, 'rank': 'C', 'num': 53, 'price': 0}, {'id': 554, 'rank': 'C', 'num': 54, 'price': 0}, {'id': 555, 'rank': 'C', 'num': 55, 'price': 0}, {'id': 556, 'rank': 'C', 'num': 56, 'price': 0}, {'id': 557, 'rank': 'C', 'num': 57, 'price': 0}, {'id': 558, 'rank': 'C', 'num': 58, 'price': 0}, {'id': 559, 'rank': 'C', 'num': 59, 'price': 0}, {'id': 560, 'rank': 'C', 'num': 60, 'price': 0}, {'id': 561, 'rank': 'C', 'num': 61, 'price': 0}, {'id': 562, 'rank': 'C', 'num': 62, 'price': 0}, {'id': 563, 'rank': 'C', 'num': 63, 'price': 0}, {'id': 564, 'rank': 'C', 'num': 64, 'price': 0}, {'id': 565, 'rank': 'C', 'num': 65, 'price': 0}, {'id': 566, 'rank': 'C', 'num': 66, 'price': 0}, {'id': 567, 'rank': 'C', 'num': 67, 'price': 0}, {'id': 568, 'rank': 'C', 'num': 68, 'price': 0}, {'id': 569, 'rank': 'C', 'num': 69, 'price': 0}, {'id': 570, 'rank': 'C', 'num': 70, 'price': 0}, {'id': 571, 'rank': 'C', 'num': 71, 'price': 0}, {'id': 572, 'rank': 'C', 'num': 72, 'price': 0}, {'id': 573, 'rank': 'C', 'num': 73, 'price': 0}, {'id': 574, 'rank': 'C', 'num': 74, 'price': 0}, {'id': 575, 'rank': 'C', 'num': 75, 'price': 0}, {'id': 576, 'rank': 'C', 'num': 76, 'price': 0}, {'id': 577, 'rank': 'C', 'num': 77, 'price': 0}, {'id': 578, 'rank': 'C', 'num': 78, 'price': 0}, {'id': 579, 'rank': 'C', 'num': 79, 'price': 0}, {'id': 580, 'rank': 'C', 'num': 80, 'price': 0}, {'id': 581, 'rank': 'C', 'num': 81, 'price': 0}, {'id': 582, 'rank': 'C', 'num': 82, 'price': 0}, {'id': 583, 'rank': 'C', 'num': 83, 'price': 0}, {'id': 584, 'rank': 'C', 'num': 84, 'price': 0}, {'id': 585, 'rank': 'C', 'num': 85, 'price': 0}, {'id': 586, 'rank': 'C', 'num': 86, 'price': 0}, {'id': 587, 'rank': 'C', 'num': 87, 'price': 0}, {'id': 588, 'rank': 'C', 'num': 88, 'price': 0}, {'id': 589, 'rank': 'C', 'num': 89, 'price': 0}, {'id': 590, 'rank': 'C', 'num': 90, 'price': 0}, {'id': 591, 'rank': 'C', 'num': 91, 'price': 0}, {'id': 592, 'rank': 'C', 'num': 92, 'price': 0}, {'id': 593, 'rank': 'C', 'num': 93, 'price': 0}, {'id': 594, 'rank': 'C', 'num': 94, 'price': 0}, {'id': 595, 'rank': 'C', 'num': 95, 'price': 0}, {'id': 596, 'rank': 'C', 'num': 96, 'price': 0}, {'id': 597, 'rank': 'C', 'num': 97, 'price': 0}, {'id': 598, 'rank': 'C', 'num': 98, 'price': 0}, {'id': 599, 'rank': 'C', 'num': 99, 'price': 0}, {'id': 600, 'rank': 'C', 'num': 100, 'price': 0}, {'id': 601, 'rank': 'C', 'num': 101, 'price': 0}, {'id': 602, 'rank': 'C', 'num': 102, 'price': 0}, {'id': 603, 'rank': 'C', 'num': 103, 'price': 0}, {'id': 604, 'rank': 'C', 'num': 104, 'price': 0}, {'id': 605, 'rank': 'C', 'num': 105, 'price': 0}, {'id': 606, 'rank': 'C', 'num': 106, 'price': 0}, {'id': 607, 'rank': 'C', 'num': 107, 'price': 0}, {'id': 608, 'rank': 'C', 'num': 108, 'price': 0}, {'id': 609, 'rank': 'C', 'num': 109, 'price': 0}, {'id': 610, 'rank': 'C', 'num': 110, 'price': 0}, {'id': 611, 'rank': 'C', 'num': 111, 'price': 0}, {'id': 612, 'rank': 'C', 'num': 112, 'price': 0}, {'id': 613, 'rank': 'C', 'num': 113, 'price': 0}, {'id': 614, 'rank': 'C', 'num': 114, 'price': 0}, {'id': 615, 'rank': 'C', 'num': 115, 'price': 0}, {'id': 616, 'rank': 'C', 'num': 116, 'price': 0}, {'id': 617, 'rank': 'C', 'num': 117, 'price': 0}, {'id': 618, 'rank': 'C', 'num': 118, 'price': 0}, {'id': 619, 'rank': 'C', 'num': 119, 'price': 0}, {'id': 620, 'rank': 'C', 'num': 120, 'price': 0}, {'id': 621, 'rank': 'C', 'num': 121, 'price': 0}, {'id': 622, 'rank': 'C', 'num': 122, 'price': 0}, {'id': 623, 'rank': 'C', 'num': 123, 'price': 0}, {'id': 624, 'rank': 'C', 'num': 124, 'price': 0}, {'id': 625, 'rank': 'C', 'num': 125, 'price': 0}, {'id': 626, 'rank': 'C', 'num': 126, 'price': 0}, {'id': 627, 'rank': 'C', 'num': 127, 'price': 0}, {'id': 628, 'rank': 'C', 'num': 128, 'price': 0}, {'id': 629, 'rank': 'C', 'num': 129, 'price': 0}, {'id': 630, 'rank': 'C', 'num': 130, 'price': 0}, {'id': 631, 'rank': 'C', 'num': 131, 'price': 0}, {'id': 632, 'rank': 'C', 'num': 132, 'price': 0}, {'id': 633, 'rank': 'C', 'num': 133, 'price': 0}, {'id': 634, 'rank': 'C', 'num': 134, 'price': 0}, {'id': 635, 'rank': 'C', 'num': 135, 'price': 0}, {'id': 636, 'rank': 'C', 'num': 136, 'price': 0}, {'id': 637, 'rank': 'C', 'num': 137, 'price': 0}, {'id': 638, 'rank': 'C', 'num': 138, 'price': 0}, {'id': 639, 'rank': 'C', 'num': 139, 'price': 0}, {'id': 640, 'rank': 'C', 'num': 140, 'price': 0}, {'id': 641, 'rank': 'C', 'num': 141, 'price': 0}, {'id': 642, 'rank': 'C', 'num': 142, 'price': 0}, {'id': 643, 'rank': 'C', 'num': 143, 'price': 0}, {'id': 644, 'rank': 'C', 'num': 144, 'price': 0}, {'id': 645, 'rank': 'C', 'num': 145, 'price': 0}, {'id': 646, 'rank': 'C', 'num': 146, 'price': 0}, {'id': 647, 'rank': 'C', 'num': 147, 'price': 0}, {'id': 648, 'rank': 'C', 'num': 148, 'price': 0}, {'id': 649, 'rank': 'C', 'num': 149, 'price': 0}, {'id': 650, 'rank': 'C', 'num': 150, 'price': 0}, {'id': 651, 'rank': 'C', 'num': 151, 'price': 0}, {'id': 652, 'rank': 'C', 'num': 152, 'price': 0}, {'id': 653, 'rank': 'C', 'num': 153, 'price': 0}, {'id': 654, 'rank': 'C', 'num': 154, 'price': 0}, {'id': 655, 'rank': 'C', 'num': 155, 'price': 0}, {'id': 656, 'rank': 'C', 'num': 156, 'price': 0}, {'id': 657, 'rank': 'C', 'num': 157, 'price': 0}, {'id': 658, 'rank': 'C', 'num': 158, 'price': 0}, {'id': 659, 'rank': 'C', 'num': 159, 'price': 0}, {'id': 660, 'rank': 'C', 'num': 160, 'price': 0}, {'id': 661, 'rank': 'C', 'num': 161, 'price': 0}, {'id': 662, 'rank': 'C', 'num': 162, 'price': 0}, {'id': 663, 'rank': 'C', 'num': 163, 'price': 0}, {'id': 664, 'rank': 'C', 'num': 164, 'price': 0}, {'id': 665, 'rank': 'C', 'num': 165, 'price': 0}, {'id': 666, 'rank': 'C', 'num': 166, 'price': 0}, {'id': 667, 'rank': 'C', 'num': 167, 'price': 0}, {'id': 668, 'rank': 'C', 'num': 168, 'price': 0}, {'id': 669, 'rank': 'C', 'num': 169, 'price': 0}, {'id': 670, 'rank': 'C', 'num': 170, 'price': 0}, {'id': 671, 'rank': 'C', 'num': 171, 'price': 0}, {'id': 672, 'rank': 'C', 'num': 172, 'price': 0}, {'id': 673, 'rank': 'C', 'num': 173, 'price': 0}, {'id': 674, 'rank': 'C', 'num': 174, 'price': 0}, {'id': 675, 'rank': 'C', 'num': 175, 'price': 0}, {'id': 676, 'rank': 'C', 'num': 176, 'price': 0}, {'id': 677, 'rank': 'C', 'num': 177, 'price': 0}, {'id': 678, 'rank': 'C', 'num': 178, 'price': 0}, {'id': 679, 'rank': 'C', 'num': 179, 'price': 0}, {'id': 680, 'rank': 'C', 'num': 180, 'price': 0}, {'id': 681, 'rank': 'C', 'num': 181, 'price': 0}, {'id': 682, 'rank': 'C', 'num': 182, 'price': 0}, {'id': 683, 'rank': 'C', 'num': 183, 'price': 0}, {'id': 684, 'rank': 'C', 'num': 184, 'price': 0}, {'id': 685, 'rank': 'C', 'num': 185, 'price': 0}, {'id': 686, 'rank': 'C', 'num': 186, 'price': 0}, {'id': 687, 'rank': 'C', 'num': 187, 'price': 0}, {'id': 688, 'rank': 'C', 'num': 188, 'price': 0}, {'id': 689, 'rank': 'C', 'num': 189, 'price': 0}, {'id': 690, 'rank': 'C', 'num': 190, 'price': 0}, {'id': 691, 'rank': 'C', 'num': 191, 'price': 0}, {'id': 692, 'rank': 'C', 'num': 192, 'price': 0}, {'id': 693, 'rank': 'C', 'num': 193, 'price': 0}, {'id': 694, 'rank': 'C', 'num': 194, 'price': 0}, {'id': 695, 'rank': 'C', 'num': 195, 'price': 0}, {'id': 696, 'rank': 'C', 'num': 196, 'price': 0}, {'id': 697, 'rank': 'C', 'num': 197, 'price': 0}, {'id': 698, 'rank': 'C', 'num': 198, 'price': 0}, {'id': 699, 'rank': 'C', 'num': 199, 'price': 0}, {'id': 700, 'rank': 'C', 'num': 200, 'price': 0}, {'id': 701, 'rank': 'C', 'num': 201, 'price': 0}, {'id': 702, 'rank': 'C', 'num': 202, 'price': 0}, {'id': 703, 'rank': 'C', 'num': 203, 'price': 0}, {'id': 704, 'rank': 'C', 'num': 204, 'price': 0}, {'id': 705, 'rank': 'C', 'num': 205, 'price': 0}, {'id': 706, 'rank': 'C', 'num': 206, 'price': 0}, {'id': 707, 'rank': 'C', 'num': 207, 'price': 0}, {'id': 708, 'rank': 'C', 'num': 208, 'price': 0}, {'id': 709, 'rank': 'C', 'num': 209, 'price': 0}, {'id': 710, 'rank': 'C', 'num': 210, 'price': 0}, {'id': 711, 'rank': 'C', 'num': 211, 'price': 0}, {'id': 712, 'rank': 'C', 'num': 212, 'price': 0}, {'id': 713, 'rank': 'C', 'num': 213, 'price': 0}, {'id': 714, 'rank': 'C', 'num': 214, 'price': 0}, {'id': 715, 'rank': 'C', 'num': 215, 'price': 0}, {'id': 716, 'rank': 'C', 'num': 216, 'price': 0}, {'id': 717, 'rank': 'C', 'num': 217, 'price': 0}, {'id': 718, 'rank': 'C', 'num': 218, 'price': 0}, {'id': 719, 'rank': 'C', 'num': 219, 'price': 0}, {'id': 720, 'rank': 'C', 'num': 220, 'price': 0}, {'id': 721, 'rank': 'C', 'num': 221, 'price': 0}, {'id': 722, 'rank': 'C', 'num': 222, 'price': 0}, {'id': 723, 'rank': 'C', 'num': 223, 'price': 0}, {'id': 724, 'rank': 'C', 'num': 224, 'price': 0}, {'id': 725, 'rank': 'C', 'num': 225, 'price': 0}, {'id': 726, 'rank': 'C', 'num': 226, 'price': 0}, {'id': 727, 'rank': 'C', 'num': 227, 'price': 0}, {'id': 728, 'rank': 'C', 'num': 228, 'price': 0}, {'id': 729, 'rank': 'C', 'num': 229, 'price': 0}, {'id': 730, 'rank': 'C', 'num': 230, 'price': 0}, {'id': 731, 'rank': 'C', 'num': 231, 'price': 0}, {'id': 732, 'rank': 'C', 'num': 232, 'price': 0}, {'id': 733, 'rank': 'C', 'num': 233, 'price': 0}, {'id': 734, 'rank': 'C', 'num': 234, 'price': 0}, {'id': 735, 'rank': 'C', 'num': 235, 'price': 0}, {'id': 736, 'rank': 'C', 'num': 236, 'price': 0}, {'id': 737, 'rank': 'C', 'num': 237, 'price': 0}, {'id': 738, 'rank': 'C', 'num': 238, 'price': 0}, {'id': 739, 'rank': 'C', 'num': 239, 'price': 0}, {'id': 740, 'rank': 'C', 'num': 240, 'price': 0}, {'id': 741, 'rank': 'C', 'num': 241, 'price': 0}, {'id': 742, 'rank': 'C', 'num': 242, 'price': 0}, {'id': 743, 'rank': 'C', 'num': 243, 'price': 0}, {'id': 744, 'rank': 'C', 'num': 244, 'price': 0}, {'id': 745, 'rank': 'C', 'num': 245, 'price': 0}, {'id': 746, 'rank': 'C', 'num': 246, 'price': 0}, {'id': 747, 'rank': 'C', 'num': 247, 'price': 0}, {'id': 748, 'rank': 'C', 'num': 248, 'price': 0}, {'id': 749, 'rank': 'C', 'num': 249, 'price': 0}, {'id': 750, 'rank': 'C', 'num': 250, 'price': 0}, {'id': 751, 'rank': 'C', 'num': 251, 'price': 0}, {'id': 752, 'rank': 'C', 'num': 252, 'price': 0}, {'id': 753, 'rank': 'C', 'num': 253, 'price': 0}, {'id': 754, 'rank': 'C', 'num': 254, 'price': 0}, {'id': 755, 'rank': 'C', 'num': 255, 'price': 0}, {'id': 756, 'rank': 'C', 'num': 256, 'price': 0}, {'id': 757, 'rank': 'C', 'num': 257, 'price': 0}, {'id': 758, 'rank': 'C', 'num': 258, 'price': 0}, {'id': 759, 'rank': 'C', 'num': 259, 'price': 0}, {'id': 760, 'rank': 'C', 'num': 260, 'price': 0}, {'id': 761, 'rank': 'C', 'num': 261, 'price': 0}, {'id': 762, 'rank': 'C', 'num': 262, 'price': 0}, {'id': 763, 'rank': 'C', 'num': 263, 'price': 0}, {'id': 764, 'rank': 'C', 'num': 264, 'price': 0}, {'id': 765, 'rank': 'C', 'num': 265, 'price': 0}, {'id': 766, 'rank': 'C', 'num': 266, 'price': 0}, {'id': 767, 'rank': 'C', 'num': 267, 'price': 0}, {'id': 768, 'rank': 'C', 'num': 268, 'price': 0}, {'id': 769, 'rank': 'C', 'num': 269, 'price': 0}, {'id': 770, 'rank': 'C', 'num': 270, 'price': 0}, {'id': 771, 'rank': 'C', 'num': 271, 'price': 0}, {'id': 772, 'rank': 'C', 'num': 272, 'price': 0}, {'id': 773, 'rank': 'C', 'num': 273, 'price': 0}, {'id': 774, 'rank': 'C', 'num': 274, 'price': 0}, {'id': 775, 'rank': 'C', 'num': 275, 'price': 0}, {'id': 776, 'rank': 'C', 'num': 276, 'price': 0}, {'id': 777, 'rank': 'C', 'num': 277, 'price': 0}, {'id': 778, 'rank': 'C', 'num': 278, 'price': 0}, {'id': 779, 'rank': 'C', 'num': 279, 'price': 0}, {'id': 780, 'rank': 'C', 'num': 280, 'price': 0}, {'id': 781, 'rank': 'C', 'num': 281, 'price': 0}, {'id': 782, 'rank': 'C', 'num': 282, 'price': 0}, {'id': 783, 'rank': 'C', 'num': 283, 'price': 0}, {'id': 784, 'rank': 'C', 'num': 284, 'price': 0}, {'id': 785, 'rank': 'C', 'num': 285, 'price': 0}, {'id': 786, 'rank': 'C', 'num': 286, 'price': 0}, {'id': 787, 'rank': 'C', 'num': 287, 'price': 0}, {'id': 788, 'rank': 'C', 'num': 288, 'price': 0}, {'id': 789, 'rank': 'C', 'num': 289, 'price': 0}, {'id': 790, 'rank': 'C', 'num': 290, 'price': 0}, {'id': 791, 'rank': 'C', 'num': 291, 'price': 0}, {'id': 792, 'rank': 'C', 'num': 292, 'price': 0}, {'id': 793, 'rank': 'C', 'num': 293, 'price': 0}, {'id': 794, 'rank': 'C', 'num': 294, 'price': 0}, {'id': 795, 'rank': 'C', 'num': 295, 'price': 0}, {'id': 796, 'rank': 'C', 'num': 296, 'price': 0}, {'id': 797, 'rank': 'C', 'num': 297, 'price': 0}, {'id': 798, 'rank': 'C', 'num': 298, 'price': 0}, {'id': 799, 'rank': 'C', 'num': 299, 'price': 0}, {'id': 800, 'rank': 'C', 'num': 300, 'price': 0}, {'id': 801, 'rank': 'C', 'num': 301, 'price': 0}, {'id': 802, 'rank': 'C', 'num': 302, 'price': 0}, {'id': 803, 'rank': 'C', 'num': 303, 'price': 0}, {'id': 804, 'rank': 'C', 'num': 304, 'price': 0}, {'id': 805, 'rank': 'C', 'num': 305, 'price': 0}, {'id': 806, 'rank': 'C', 'num': 306, 'price': 0}, {'id': 807, 'rank': 'C', 'num': 307, 'price': 0}, {'id': 808, 'rank': 'C', 'num': 308, 'price': 0}, {'id': 809, 'rank': 'C', 'num': 309, 'price': 0}, {'id': 810, 'rank': 'C', 'num': 310, 'price': 0}, {'id': 811, 'rank': 'C', 'num': 311, 'price': 0}, {'id': 812, 'rank': 'C', 'num': 312, 'price': 0}, {'id': 813, 'rank': 'C', 'num': 313, 'price': 0}, {'id': 814, 'rank': 'C', 'num': 314, 'price': 0}, {'id': 815, 'rank': 'C', 'num': 315, 'price': 0}, {'id': 816, 'rank': 'C', 'num': 316, 'price': 0}, {'id': 817, 'rank': 'C', 'num': 317, 'price': 0}, {'id': 818, 'rank': 'C', 'num': 318, 'price': 0}, {'id': 819, 'rank': 'C', 'num': 319, 'price': 0}, {'id': 820, 'rank': 'C', 'num': 320, 'price': 0}, {'id': 821, 'rank': 'C', 'num': 321, 'price': 0}, {'id': 822, 'rank': 'C', 'num': 322, 'price': 0}, {'id': 823, 'rank': 'C', 'num': 323, 'price': 0}, {'id': 824, 'rank': 'C', 'num': 324, 'price': 0}, {'id': 825, 'rank': 'C', 'num': 325, 'price': 0}, {'id': 826, 'rank': 'C', 'num': 326, 'price': 0}, {'id': 827, 'rank': 'C', 'num': 327, 'price': 0}, {'id': 828, 'rank': 'C', 'num': 328, 'price': 0}, {'id': 829, 'rank': 'C', 'num': 329, 'price': 0}, {'id': 830, 'rank': 'C', 'num': 330, 'price': 0}, {'id': 831, 'rank': 'C', 'num': 331, 'price': 0}, {'id': 832, 'rank': 'C', 'num': 332, 'price': 0}, {'id': 833, 'rank': 'C', 'num': 333, 'price': 0}, {'id': 834, 'rank': 'C', 'num': 334, 'price': 0}, {'id': 835, 'rank': 'C', 'num': 335, 'price': 0}, {'id': 836, 'rank': 'C', 'num': 336, 'price': 0}, {'id': 837, 'rank': 'C', 'num': 337, 'price': 0}, {'id': 838, 'rank': 'C', 'num': 338, 'price': 0}, {'id': 839, 'rank': 'C', 'num': 339, 'price': 0}, {'id': 840, 'rank': 'C', 'num': 340, 'price': 0}, {'id': 841, 'rank': 'C', 'num': 341, 'price': 0}, {'id': 842, 'rank': 'C', 'num': 342, 'price': 0}, {'id': 843, 'rank': 'C', 'num': 343, 'price': 0}, {'id': 844, 'rank': 'C', 'num': 344, 'price': 0}, {'id': 845, 'rank': 'C', 'num': 345, 'price': 0}, {'id': 846, 'rank': 'C', 'num': 346, 'price': 0}, {'id': 847, 'rank': 'C', 'num': 347, 'price': 0}, {'id': 848, 'rank': 'C', 'num': 348, 'price': 0}, {'id': 849, 'rank': 'C', 'num': 349, 'price': 0}, {'id': 850, 'rank': 'C', 'num': 350, 'price': 0}, {'id': 851, 'rank': 'C', 'num': 351, 'price': 0}, {'id': 852, 'rank': 'C', 'num': 352, 'price': 0}, {'id': 853, 'rank': 'C', 'num': 353, 'price': 0}, {'id': 854, 'rank': 'C', 'num': 354, 'price': 0}, {'id': 855, 'rank': 'C', 'num': 355, 'price': 0}, {'id': 856, 'rank': 'C', 'num': 356, 'price': 0}, {'id': 857, 'rank': 'C', 'num': 357, 'price': 0}, {'id': 858, 'rank': 'C', 'num': 358, 'price': 0}, {'id': 859, 'rank': 'C', 'num': 359, 'price': 0}, {'id': 860, 'rank': 'C', 'num': 360, 'price': 0}, {'id': 861, 'rank': 'C', 'num': 361, 'price': 0}, {'id': 862, 'rank': 'C', 'num': 362, 'price': 0}, {'id': 863, 'rank': 'C', 'num': 363, 'price': 0}, {'id': 864, 'rank': 'C', 'num': 364, 'price': 0}, {'id': 865, 'rank': 'C', 'num': 365, 'price': 0}, {'id': 866, 'rank': 'C', 'num': 366, 'price': 0}, {'id': 867, 'rank': 'C', 'num': 367, 'price': 0}, {'id': 868, 'rank': 'C', 'num': 368, 'price': 0}, {'id': 869, 'rank': 'C', 'num': 369, 'price': 0}, {'id': 870, 'rank': 'C', 'num': 370, 'price': 0}, {'id': 871, 'rank': 'C', 'num': 371, 'price': 0}, {'id': 872, 'rank': 'C', 'num': 372, 'price': 0}, {'id': 873, 'rank': 'C', 'num': 373, 'price': 0}, {'id': 874, 'rank': 'C', 'num': 374, 'price': 0}, {'id': 875, 'rank': 'C', 'num': 375, 'price': 0}, {'id': 876, 'rank': 'C', 'num': 376, 'price': 0}, {'id': 877, 'rank': 'C', 'num': 377, 'price': 0}, {'id': 878, 'rank': 'C', 'num': 378, 'price': 0}, {'id': 879, 'rank': 'C', 'num': 379, 'price': 0}, {'id': 880, 'rank': 'C', 'num': 380, 'price': 0}, {'id': 881, 'rank': 'C', 'num': 381, 'price': 0}, {'id': 882, 'rank': 'C', 'num': 382, 'price': 0}, {'id': 883, 'rank': 'C', 'num': 383, 'price': 0}, {'id': 884, 'rank': 'C', 'num': 384, 'price': 0}, {'id': 885, 'rank': 'C', 'num': 385, 'price': 0}, {'id': 886, 'rank': 'C', 'num': 386, 'price': 0}, {'id': 887, 'rank': 'C', 'num': 387, 'price': 0}, {'id': 888, 'rank': 'C', 'num': 388, 'price': 0}, {'id': 889, 'rank': 'C', 'num': 389, 'price': 0}, {'id': 890, 'rank': 'C', 'num': 390, 'price': 0}, {'id': 891, 'rank': 'C', 'num': 391, 'price': 0}, {'id': 892, 'rank': 'C', 'num': 392, 'price': 0}, {'id': 893, 'rank': 'C', 'num': 393, 'price': 0}, {'id': 894, 'rank': 'C', 'num': 394, 'price': 0}, {'id': 895, 'rank': 'C', 'num': 395, 'price': 0}, {'id': 896, 'rank': 'C', 'num': 396, 'price': 0}, {'id': 897, 'rank': 'C', 'num': 397, 'price': 0}, {'id': 898, 'rank': 'C', 'num': 398, 'price': 0}, {'id': 899, 'rank': 'C', 'num': 399, 'price': 0}, {'id': 900, 'rank': 'C', 'num': 400, 'price': 0}, {'id': 901, 'rank': 'C', 'num': 401, 'price': 0}, {'id': 902, 'rank': 'C', 'num': 402, 'price': 0}, {'id': 903, 'rank': 'C', 'num': 403, 'price': 0}, {'id': 904, 'rank': 'C', 'num': 404, 'price': 0}, {'id': 905, 'rank': 'C', 'num': 405, 'price': 0}, {'id': 906, 'rank': 'C', 'num': 406, 'price': 0}, {'id': 907, 'rank': 'C', 'num': 407, 'price': 0}, {'id': 908, 'rank': 'C', 'num': 408, 'price': 0}, {'id': 909, 'rank': 'C', 'num': 409, 'price': 0}, {'id': 910, 'rank': 'C', 'num': 410, 'price': 0}, {'id': 911, 'rank': 'C', 'num': 411, 'price': 0}, {'id': 912, 'rank': 'C', 'num': 412, 'price': 0}, {'id': 913, 'rank': 'C', 'num': 413, 'price': 0}, {'id': 914, 'rank': 'C', 'num': 414, 'price': 0}, {'id': 915, 'rank': 'C', 'num': 415, 'price': 0}, {'id': 916, 'rank': 'C', 'num': 416, 'price': 0}, {'id': 917, 'rank': 'C', 'num': 417, 'price': 0}, {'id': 918, 'rank': 'C', 'num': 418, 'price': 0}, {'id': 919, 'rank': 'C', 'num': 419, 'price': 0}, {'id': 920, 'rank': 'C', 'num': 420, 'price': 0}, {'id': 921, 'rank': 'C', 'num': 421, 'price': 0}, {'id': 922, 'rank': 'C', 'num': 422, 'price': 0}, {'id': 923, 'rank': 'C', 'num': 423, 'price': 0}, {'id': 924, 'rank': 'C', 'num': 424, 'price': 0}, {'id': 925, 'rank': 'C', 'num': 425, 'price': 0}, {'id': 926, 'rank': 'C', 'num': 426, 'price': 0}, {'id': 927, 'rank': 'C', 'num': 427, 'price': 0}, {'id': 928, 'rank': 'C', 'num': 428, 'price': 0}, {'id': 929, 'rank': 'C', 'num': 429, 'price': 0}, {'id': 930, 'rank': 'C', 'num': 430, 'price': 0}, {'id': 931, 'rank': 'C', 'num': 431, 'price': 0}, {'id': 932, 'rank': 'C', 'num': 432, 'price': 0}, {'id': 933, 'rank': 'C', 'num': 433, 'price': 0}, {'id': 934, 'rank': 'C', 'num': 434, 'price': 0}, {'id': 935, 'rank': 'C', 'num': 435, 'price': 0}, {'id': 936, 'rank': 'C', 'num': 436, 'price': 0}, {'id': 937, 'rank': 'C', 'num': 437, 'price': 0}, {'id': 938, 'rank': 'C', 'num': 438, 'price': 0}, {'id': 939, 'rank': 'C', 'num': 439, 'price': 0}, {'id': 940, 'rank': 'C', 'num': 440, 'price': 0}, {'id': 941, 'rank': 'C', 'num': 441, 'price': 0}, {'id': 942, 'rank': 'C', 'num': 442, 'price': 0}, {'id': 943, 'rank': 'C', 'num': 443, 'price': 0}, {'id': 944, 'rank': 'C', 'num': 444, 'price': 0}, {'id': 945, 'rank': 'C', 'num': 445, 'price': 0}, {'id': 946, 'rank': 'C', 'num': 446, 'price': 0}, {'id': 947, 'rank': 'C', 'num': 447, 'price': 0}, {'id': 948, 'rank': 'C', 'num': 448, 'price': 0}, {'id': 949, 'rank': 'C', 'num': 449, 'price': 0}, {'id': 950, 'rank': 'C', 'num': 450, 'price': 0}, {'id': 951, 'rank': 'C', 'num': 451, 'price': 0}, {'id': 952, 'rank': 'C', 'num': 452, 'price': 0}, {'id': 953, 'rank': 'C', 'num': 453, 'price': 0}, {'id': 954, 'rank': 'C', 'num': 454, 'price': 0}, {'id': 955, 'rank': 'C', 'num': 455, 'price': 0}, {'id': 956, 'rank': 'C', 'num': 456, 'price': 0}, {'id': 957, 'rank': 'C', 'num': 457, 'price': 0}, {'id': 958, 'rank': 'C', 'num': 458, 'price': 0}, {'id': 959, 'rank': 'C', 'num': 459, 'price': 0}, {'id': 960, 'rank': 'C', 'num': 460, 'price': 0}, {'id': 961, 'rank': 'C', 'num': 461, 'price': 0}, {'id': 962, 'rank': 'C', 'num': 462, 'price': 0}, {'id': 963, 'rank': 'C', 'num': 463, 'price': 0}, {'id': 964, 'rank': 'C', 'num': 464, 'price': 0}, {'id': 965, 'rank': 'C', 'num': 465, 'price': 0}, {'id': 966, 'rank': 'C', 'num': 466, 'price': 0}, {'id': 967, 'rank': 'C', 'num': 467, 'price': 0}, {'id': 968, 'rank': 'C', 'num': 468, 'price': 0}, {'id': 969, 'rank': 'C', 'num': 469, 'price': 0}, {'id': 970, 'rank': 'C', 'num': 470, 'price': 0}, {'id': 971, 'rank': 'C', 'num': 471, 'price': 0}, {'id': 972, 'rank': 'C', 'num': 472, 'price': 0}, {'id': 973, 'rank': 'C', 'num': 473, 'price': 0}, {'id': 974, 'rank': 'C', 'num': 474, 'price': 0}, {'id': 975, 'rank': 'C', 'num': 475, 'price': 0}, {'id': 976, 'rank': 'C', 'num': 476, 'price': 0}, {'id': 977, 'rank': 'C', 'num': 477, 'price': 0}, {'id': 978, 'rank': 'C', 'num': 478, 'price': 0}, {'id': 979, 'rank': 'C', 'num': 479, 'price': 0}, {'id': 980, 'rank': 'C', 'num': 480, 'price': 0}, {'id': 981, 'rank': 'C', 'num': 481, 'price': 0}, {'id': 982, 'rank': 'C', 'num': 482, 'price': 0}, {'id': 983, 'rank': 'C', 'num': 483, 'price': 0}, {'id': 984, 'rank': 'C', 'num': 484, 'price': 0}, {'id': 985, 'rank': 'C', 'num': 485, 'price': 0}, {'id': 986, 'rank': 'C', 'num': 486, 'price': 0}, {'id': 987, 'rank': 'C', 'num': 487, 'price': 0}, {'id': 988, 'rank': 'C', 'num': 488, 'price': 0}, {'id': 989, 'rank': 'C', 'num': 489, 'price': 0}, {'id': 990, 'rank': 'C', 'num': 490, 'price': 0}, {'id': 991, 'rank': 'C', 'num': 491, 'price': 0}, {'id': 992, 'rank': 'C', 'num': 492, 'price': 0}, {'id': 993, 'rank': 'C', 'num': 493, 'price': 0}, {'id': 994, 'rank': 'C', 'num': 494, 'price': 0}, {'id': 995, 'rank': 'C', 'num': 495, 'price': 0}, {'id': 996, 'rank': 'C', 'num': 496, 'price': 0}, {'id': 997, 'rank': 'C', 'num': 497, 'price': 0}, {'id': 998, 'rank': 'C', 'num': 498, 'price': 0}, {'id': 999, 'rank': 'C', 'num': 499, 'price': 0}, {'id': 1000, 'rank': 'C', 'num': 500, 'price': 0}, {'id': 1, 'rank': 'S', 'num': 1, 'price': 5000}, {'id': 2, 'rank': 'S', 'num': 2, 'price': 5000}, {'id': 3, 'rank': 'S', 'num': 3, 'price': 5000}, {'id': 4, 'rank': 'S', 'num': 4, 'price': 5000}, {'id': 5, 'rank': 'S', 'num': 5, 'price': 5000}, {'id': 6, 'rank': 'S', 'num': 6, 'price': 5000}, {'id': 7, 'rank': 'S', 'num': 7, 'price': 5000}, {'id': 8, 'rank': 'S', 'num': 8, 'price': 5000}, {'id': 9, 'rank': 'S', 'num': 9, 'price': 5000}, {'id': 10, 'rank': 'S', 'num': 10, 'price': 5000}, {'id': 11, 'rank': 'S', 'num': 11, 'price': 5000}, {'id': 12, 'rank': 'S', 'num': 12, 'price': 5000}, {'id': 13, 'rank': 'S', 'num': 13, 'price': 5000}, {'id': 14, 'rank': 'S', 'num': 14, 'price': 5000}, {'id': 15, 'rank': 'S', 'num': 15, 'price': 5000}, {'id': 16, 'rank': 'S', 'num': 16, 'price': 5000}, {'id': 17, 'rank': 'S', 'num': 17, 'price': 5000}, {'id': 18, 'rank': 'S', 'num': 18, 'price': 5000}, {'id': 19, 'rank': 'S', 'num': 19, 'price': 5000}, {'id': 20, 'rank': 'S', 'num': 20, 'price': 5000}, {'id': 21, 'rank': 'S', 'num': 21, 'price': 5000}, {'id': 22, 'rank': 'S', 'num': 22, 'price': 5000}, {'id': 23, 'rank': 'S', 'num': 23, 'price': 5000}, {'id': 24, 'rank': 'S', 'num': 24, 'price': 5000}, {'id': 25, 'rank': 'S', 'num': 25, 'price': 5000}, {'id': 26, 'rank': 'S', 'num': 26, 'price': 5000}, {'id': 27, 'rank': 'S', 'num': 27, 'price': 5000}, {'id': 28, 'rank': 'S', 'num': 28, 'price': 5000}, {'id': 29, 'rank': 'S', 'num': 29, 'price': 5000}, {'id': 30, 'rank': 'S', 'num': 30, 'price': 5000}, {'id': 31, 'rank': 'S', 'num': 31, 'price': 5000}, {'id': 32, 'rank': 'S', 'num': 32, 'price': 5000}, {'id': 33, 'rank': 'S', 'num': 33, 'price': 5000}, {'id': 34, 'rank': 'S', 'num': 34, 'price': 5000}, {'id': 35, 'rank': 'S', 'num': 35, 'price': 5000}, {'id': 36, 'rank': 'S', 'num': 36, 'price': 5000}, {'id': 37, 'rank': 'S', 'num': 37, 'price': 5000}, {'id': 38, 'rank': 'S', 'num': 38, 'price': 5000}, {'id': 39, 'rank': 'S', 'num': 39, 'price': 5000}, {'id': 40, 'rank': 'S', 'num': 40, 'price': 5000}, {'id': 41, 'rank': 'S', 'num': 41, 'price': 5000}, {'id': 42, 'rank': 'S', 'num': 42, 'price': 5000}, {'id': 43, 'rank': 'S', 'num': 43, 'price': 5000}, {'id': 44, 'rank': 'S', 'num': 44, 'price': 5000}, {'id': 45, 'rank': 'S', 'num': 45, 'price': 5000}, {'id': 46, 'rank': 'S', 'num': 46, 'price': 5000}, {'id': 47, 'rank': 'S', 'num': 47, 'price': 5000}, {'id': 48, 'rank': 'S', 'num': 48, 'price': 5000}, {'id': 49, 'rank': 'S', 'num': 49, 'price': 5000}, {'id': 50, 'rank': 'S', 'num': 50, 'price': 5000})

    for sheet in sheets:
        #if not event['sheets'][sheet['rank']].get('price'):
        #    event['sheets'][sheet['rank']]['price'] = event['price'] + sheet['price']
        #event['total'] += 1
        #event['sheets'][sheet['rank']]['total'] += 1

        #cur.execute(
        #    "SELECT * FROM reservations WHERE event_id = %s AND sheet_id = %s AND canceled_at #IS NULL GROUP BY event_id, sheet_id HAVING reserved_at = MIN(reserved_at)",
        #    [event['id'], sheet['id']])
        #reservation = cur.fetchone()

        ##event['remains'] -= 1
        ##event['sheets'][sheet['rank']]['remains'] -= 1
        ##if login_user_id and sheet['user_id'] == login_user_id:
        ##  sheet['mine'] = True
        ##sheet['reserved'] = True
        ##sheet['reserved_at'] = int(sheet['reserved_at'].replace(tzinfo=timezone.utc).timestamp())
        ##event['sheets'][sheet['rank']]['detail'].append(sheet)

        if sheet["id"] in reservations:
            if login_user_id and reservations[sheet["id"]]['user_id'] == login_user_id:
                sheet['mine'] = True
            sheet['reserved'] = True
            sheet['reserved_at'] = int(reservations[sheet["id"]]['reserved_at'].replace(tzinfo=timezone.utc).timestamp())
        else:
            event['remains'] += 1
            event['sheets'][sheet['rank']]['remains'] += 1

        event['sheets'][sheet['rank']]['detail'].append(sheet)

        del sheet['id']
        del sheet['price']
        del sheet['rank']

    event['public'] = True if event['public_fg'] else False
    event['closed'] = True if event['closed_fg'] else False
    del event['public_fg']
    del event['closed_fg']

    #print("return event:")
    #print(event)
    return event


def sanitize_event(event):
    sanitized = copy.copy(event)
    del sanitized['price']
    del sanitized['public']
    del sanitized['closed']
    return sanitized


def get_login_user():
    if "user_id" not in flask.session:
        return None
    cur = dbh().cursor()
    user_id = flask.session['user_id']
    cur.execute("SELECT id, nickname FROM users WHERE id = %s", [user_id])
    return cur.fetchone()


def get_login_administrator():
    if "administrator_id" not in flask.session:
        return None
    cur = dbh().cursor()
    administrator_id = flask.session['administrator_id']
    cur.execute("SELECT id, nickname FROM administrators WHERE id = %s", [administrator_id])
    return cur.fetchone()


def validate_rank(rank):
    cur = dbh().cursor()
    cur.execute("SELECT COUNT(*) AS total_sheets FROM sheets WHERE `rank` = %s", [rank])
    ret = cur.fetchone()
    return int(ret['total_sheets']) > 0


def render_report_csv(reports, prefix):
    #reports = sorted(reports, key=lambda x: x['sold_at'])

    #keys = ["reservation_id", "event_id", "rank", "num", "price", "user_id", "sold_at", "canceled_at"]

    #body = []
    #body.append(keys)
    #for report in reports:
    #    body.append([report[key] for key in keys])

    #f = StringIO()
    #writer = csv.writer(f)
    #writer.writerows(body)
    #res = flask.make_response()
    #res.data = f.getvalue()
    #res.headers['Content-Type'] = 'text/csv'
    #res.headers['Content-Disposition'] = 'attachment; filename=report.csv'
    subprocess.call("/usr/bin/scp -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -i /home/isucon/.ssh/id_rsa_isucon isucon@172.31.21.46:/var/lib/mysql/torb/{}_report.csv /tmp/".format(prefix).split())
    downloadFileName = 'report.csv'
    downloadFile = '/tmp/{}_report.csv'.format(prefix)

    return flask.send_file(downloadFile, as_attachment = True, \
        attachment_filename = downloadFileName, \
        mimetype = 'text/csv')

    #return res


@app.route('/')
def get_index():
    user = get_login_user()
    events = []
    for event in get_events(lambda e: e["public_fg"], public_fg=True):
        events.append(sanitize_event(event))
    return flask.render_template('index.html', user=user, events=events, base_url=make_base_url(flask.request))


@app.route('/initialize')
def get_initialize():
    subprocess.call(["../../db/init.sh"])
    conn = dbh()
    conn.autocommit(True)
    cur = conn.cursor()
    cur.execute("SELECT * FROM events")
    events = cur.fetchall()
    for event in events:
        cur.execute("""
            INSERT INTO sheet_reservations(event_id, sheet_id, reserved)   
            SELECT
                {0} as event_id,
                s.id as sheet_id,
                (CASE
                WHEN r.id is NULL THEN 0
                ELSE 1
                END )as reserved
            FROM
                sheets s
            LEFT OUTER JOIN
                reservations r 
            ON s.id = r.sheet_id AND event_id = {0} WHERE r.canceled_at IS NULL
            ;
        """.format(event['id']))
        conn.commit()
    return ('', 204)


@app.route('/api/users', methods=['POST'])
def post_users():
    nickname = flask.request.json['nickname']
    login_name = flask.request.json['login_name']
    password = flask.request.json['password']

    conn = dbh()
    conn.autocommit(False)
    cur = conn.cursor()
    try:
        cur.execute("SELECT * FROM users WHERE login_name = %s", [login_name])
        duplicated = cur.fetchone()
        if duplicated:
            conn.rollback()
            return res_error('duplicated', 409)
        cur.execute(
            "INSERT INTO users (login_name, pass_hash, nickname) VALUES (%s, SHA2(%s, 256), %s)",
            [login_name, password, nickname])
        user_id = cur.lastrowid
        conn.commit()
    except MySQLdb.Error as e:
        conn.rollback()
        print(e)
        return res_error()
    return (jsonify({"id": user_id, "nickname": nickname}), 201)


@app.route('/api/users/<int:user_id>')
@login_required
def get_users(user_id):
    cur = dbh().cursor()
    cur.execute('SELECT id, nickname FROM users WHERE id = %s', [user_id])
    user = cur.fetchone()
    if user['id'] != get_login_user()['id']:
        return ('', 403)

    cur.execute(
        "SELECT r.*, s.rank AS sheet_rank, s.num AS sheet_num FROM reservations r INNER JOIN sheets s ON s.id = r.sheet_id WHERE r.user_id = %s ORDER BY IFNULL(r.canceled_at, r.reserved_at) DESC LIMIT 5",
        [user['id']])
    recent_reservations = []
    for row in cur.fetchall():
        event = get_event(row['event_id'])
        price = event['sheets'][row['sheet_rank']]['price']
        del event['sheets']
        del event['total']
        del event['remains']

        if row['canceled_at']:
            canceled_at = int(row['canceled_at'].replace(tzinfo=timezone.utc).timestamp())
        else:
            canceled_at = None

        recent_reservations.append({
            "id": int(row['id']),
            "event": event,
            "sheet_rank": row['sheet_rank'],
            "sheet_num": int(row['sheet_num']),
            "price": int(price),
            "reserved_at": int(row['reserved_at'].replace(tzinfo=timezone.utc).timestamp()),
            "canceled_at": canceled_at,
        })

    user['recent_reservations'] = recent_reservations
    cur.execute(
        "SELECT IFNULL(SUM(e.price + s.price), 0) AS total_price FROM reservations r INNER JOIN sheets s ON s.id = r.sheet_id INNER JOIN events e ON e.id = r.event_id WHERE r.user_id = %s AND r.canceled_at IS NULL",
        [user['id']])
    row = cur.fetchone()
    user['total_price'] = int(row['total_price'])

    cur.execute(
        "SELECT event_id FROM reservations WHERE user_id = %s GROUP BY event_id ORDER BY MAX(IFNULL(canceled_at, reserved_at)) DESC LIMIT 5",
        [user['id']])
    rows = cur.fetchall()
    recent_events = []
    for row in rows:
        event = get_event(row['event_id'])
        for sheet in event['sheets'].values():
            del sheet['detail']
        recent_events.append(event)
    user['recent_events'] = recent_events

    return jsonify(user)


@app.route('/api/actions/login', methods=['POST'])
def post_login():
    login_name = flask.request.json['login_name']
    password = flask.request.json['password']

    cur = dbh().cursor()

    cur.execute('SELECT * FROM users WHERE login_name = %s', [login_name])
    user = cur.fetchone()
    cur.execute('SELECT SHA2(%s, 256) AS pass_hash', [password])
    pass_hash = cur.fetchone()
    if not user or pass_hash['pass_hash'] != user['pass_hash']:
        return res_error("authentication_failed", 401)

    flask.session['user_id'] = user["id"]
    user = get_login_user()
    return flask.jsonify(user)


@app.route('/api/actions/logout', methods=['POST'])
@login_required
def post_logout():
    flask.session.pop('user_id', None)
    return ('', 204)


@app.route('/api/events')
def get_events_api():
    events = []
    for event in get_events(lambda e: e["public_fg"], public_fg=True):
        events.append(sanitize_event(event))
    return jsonify(events)


@app.route('/api/events/<int:event_id>')
def get_events_by_id(event_id):
    user = get_login_user()
    if user: event = get_event(event_id, user['id'])
    else: event = get_event(event_id)

    if not event or not event["public"]:
        return res_error("not_found", 404)

    event = sanitize_event(event)
    return jsonify(event)

def simple_get_event(event_id):
    cur = dbh().cursor()
    cur.execute("SELECT * FROM events WHERE id =%s",[event_id])
    event = cur.fetchone()
    if event:
        event['public'] = True if event['public_fg'] else False
        event['closed'] = True if event['closed_fg'] else False
        del event['public_fg']
        del event['closed_fg']
    return event

@app.route('/api/events/<int:event_id>/actions/reserve', methods=['POST'])
@login_required
def post_reserve(event_id):
    rank = flask.request.json["sheet_rank"]

    user = get_login_user()
    event = simple_get_event(event_id)

    if not event or not event['public']:
        return res_error("invalid_event", 404)
    if not validate_rank(rank):
        return res_error("invalid_rank", 400)

    sheet = None
    reservation_id = 0
    conn =  dbh()
    cur = conn.cursor()
    cur.execute(
        "SELECT sr.*, s.num as num FROM sheet_reservations sr join sheets s on sr.sheet_id = s.id WHERE sr.event_id ={} AND sr.reserved = 0 and s.`rank` ='{}' ORDER BY RAND()".format(event['id'], rank))
    sheet_reservations = cur.fetchall()
    idx = 0
    while True:
        if len(sheet_reservations) == idx:
            return res_error("sold_out", 409)
        cur.execute(
            "SELECT * FROM sheet_reservations WHERE id =%s AND reserved = 0 FOR UPDATE",
            [sheet_reservations[idx]['id']])
        sheet_reservation_with_trx = cur.fetchone()
        if not sheet_reservation_with_trx:
            conn.rollback()
            idx += 1
            continue
        try:
            conn.autocommit(False)
            cur = conn.cursor()
            cur.execute("""
            UPDATE sheet_reservations SET reserved = 1 WHERE id ={}
            """.format(sheet_reservations[idx]['id']))
            cur.execute(
                "INSERT INTO reservations (event_id, sheet_id, user_id, reserved_at) VALUES (%s, %s, %s, %s)",
                [event['id'], sheet_reservation_with_trx['sheet_id'], user['id'], datetime.utcnow().strftime("%F %T.%f")])
            reservation_id = cur.lastrowid
            conn.commit()
        except MySQLdb.Error as e:
            conn.rollback()
            print(e)
        break

    content = jsonify({
        "id": reservation_id,
        "sheet_rank": rank,
        "sheet_num": sheet_reservations[idx]['num']})
    return flask.Response(content, status=202, mimetype='application/json')


@app.route('/api/events/<int:event_id>/sheets/<rank>/<int:num>/reservation', methods=['DELETE'])
@login_required
def delete_reserve(event_id, rank, num):
    user = get_login_user()
    event = simple_get_event(event_id)

    if not event or not event['public']:
        return res_error("invalid_event", 404)
    if not validate_rank(rank):
        return res_error("invalid_rank", 404)

    cur = dbh().cursor()
    cur.execute('SELECT * FROM sheets WHERE `rank` = %s AND num = %s', [rank, num])
    sheet = cur.fetchone()
    if not sheet:
        return res_error("invalid_sheet", 404)

    try:
        conn = dbh()
        conn.autocommit(False)
        cur = conn.cursor() # begin
        cur.execute("""
            UPDATE sheet_reservations SET reserved = 0 WHERE sheet_id = {} AND event_id = {}
            """.format(sheet['id'], event['id']))
        cur.execute(
            "SELECT * FROM reservations WHERE event_id = %s AND sheet_id = %s AND canceled_at IS NULL",
            [event['id'], sheet['id']])
        reservation = cur.fetchone()

        if not reservation:
            conn.rollback()
            return res_error("not_reserved", 400)
        if reservation['user_id'] != user['id']:
            conn.rollback()
            return res_error("not_permitted", 403)

        
        cur.execute(
            "UPDATE reservations SET canceled_at = %s WHERE id = %s and canceled_at IS NULL",
            [datetime.utcnow().strftime("%F %T.%f"), reservation['id']])
        conn.commit()
    except MySQLdb.Error as e:
        conn.rollback()
        print(e)
        return res_error()

    return flask.Response(status=204)


@app.route('/admin/')
def get_admin():
    administrator = get_login_administrator()
    if administrator: events=get_events()
    else: events={}
    return flask.render_template('admin.html', administrator=administrator, events=events, base_url=make_base_url(flask.request))


@app.route('/admin/api/actions/login', methods=['POST'])
def post_adin_login():
    login_name = flask.request.json['login_name']
    password = flask.request.json['password']

    cur = dbh().cursor()

    cur.execute('SELECT * FROM administrators WHERE login_name = %s', [login_name])
    administrator = cur.fetchone()
    cur.execute('SELECT SHA2(%s, 256) AS pass_hash', [password])
    pass_hash = cur.fetchone()

    if not administrator or pass_hash['pass_hash'] != administrator['pass_hash']:
        return res_error("authentication_failed", 401)

    flask.session['administrator_id'] = administrator['id']
    administrator = get_login_administrator()
    return jsonify(administrator)


@app.route('/admin/api/actions/logout', methods=['POST'])
@admin_login_required
def get_admin_logout():
    flask.session.pop('administrator_id', None)
    return ('', 204)


@app.route('/admin/api/events')
@admin_login_required
def get_admin_events_api():
    return jsonify(get_events())


@app.route('/admin/api/events', methods=['POST'])
@admin_login_required
def post_admin_events_api():
    title = flask.request.json['title']
    public = flask.request.json['public']
    price = flask.request.json['price']

    conn = dbh()
    conn.autocommit(False)
    cur = conn.cursor()
    try:
        cur.execute(
            "INSERT INTO events (title, public_fg, closed_fg, price) VALUES (%s, %s, 0, %s)",
            [title, public, price])
        event_id = cur.lastrowid
        conn.commit()
        cur.execute("""
            INSERT INTO sheet_reservations(event_id, sheet_id)   
            SELECT
                {0} as event_id,
                s.id as sheet_id
            FROM
                sheets s
            ;
        """.format(event_id))
        conn.commit()
    except MySQLdb.Error as e:
        conn.rollback()
        print(e)
    return jsonify(get_event(event_id))


@app.route('/admin/api/events/<int:event_id>')
@admin_login_required
def get_admin_events_by_id(event_id):
    event = simple_get_event(event_id)
    if not event:
        return res_error("not_found", 404)
    return jsonify(event)


@app.route('/admin/api/events/<int:event_id>/actions/edit', methods=['POST'])
@admin_login_required
def post_event_edit(event_id):
    public = flask.request.json['public'] if 'public' in flask.request.json.keys() else False
    closed = flask.request.json['closed'] if 'closed' in flask.request.json.keys() else False
    if closed: public = False

    event = simple_get_event(event_id)
    if not event:
        return res_error("not_found", 404)

    if event['closed']:
        return res_error('cannot_edit_closed_event', 400)
    elif event['public'] and closed:
        return res_error('cannot_close_public_event', 400)

    conn = dbh()
    conn.autocommit(False)
    cur = conn.cursor()
    try:
        cur.execute(
            "UPDATE events SET public_fg = %s, closed_fg = %s WHERE id = %s",
            [public, closed, event['id']])
        conn.commit()
    except MySQLdb.Error as e:
        conn.rollback()
    return jsonify(get_event(event_id))


@app.route('/admin/api/reports/events/<int:event_id>/sales')
@admin_login_required
def get_admin_event_sales(event_id):
    prefix = str(uuid.uuid4())
    event = simple_get_event(event_id)

    cur = dbh().cursor()
    reservations = cur.execute("""
    SELECT 'reservation_id', 'event_id', 'rank', 'num', 'price', 'user_id', 'sold_at', 'canceled_at' union
    (SELECT
        r.id as reservation_id
        ,e.id    AS event_id
        ,s.rank  AS rank
        ,s.num   AS num
        ,e.price + s.price AS price
        ,r.user_id as user_id
        ,DATE_FORMAT(r.reserved_at, '%Y-%m-%dT%H:%i:%s.%fZ') as sold_at
        ,IFNULL(DATE_FORMAT(r.canceled_at, '%Y-%m-%dT%H:%i:%s.%fZ'), '') as canceled_at
    INTO OUTFILE '{0}_report.csv'
    FIELDS TERMINATED BY ','
    LINES TERMINATED BY '\n'
    FROM reservations r
    INNER JOIN sheets s
    ON s.id = r.sheet_id
    INNER JOIN events e
    ON e.id = r.event_id
    WHERE r.event_id = {1}
    ORDER BY reserved_at ASC);
    """.format(prefix,event['id']))
    reservations = cur.fetchall()
    #reports = []

#    for reservation in reservations:
#        if reservation['canceled_at']:
#            canceled_at = reservation['canceled_at'].isoformat()+"Z"
#        else: canceled_at = ''
#        reports.append({
#            "reservation_id": reservation['id'],
#            "event_id":       event['id'],
#            "rank":           reservation['sheet_rank'],
#            "num":            reservation['sheet_num'],
#            "user_id":        reservation['user_id'],
#            "sold_at":        reservation['reserved_at'].isoformat()+"Z",
#            "canceled_at":    canceled_at,
#            "price":          reservation['event_price'] + reservation['sheet_price'],
#        })

    return render_report_csv(reservations, prefix)


@app.route('/admin/api/reports/sales')
@admin_login_required
def get_admin_sales():
    prefix = str(uuid.uuid4())
    cur = dbh().cursor()
    reservations = cur.execute('''
      SELECT 'reservation_id', 'event_id', 'rank', 'num', 'price', 'user_id', 'sold_at', 'canceled_at' union
      (SELECT
          r.id as reservation_id
          ,e.id    AS event_id
          ,s.rank  AS rank
          ,s.num   AS num
          ,e.price + s.price AS price
          ,r.user_id as user_id
          ,DATE_FORMAT(r.reserved_at, '%Y-%m-%dT%H:%i:%s.%fZ') as sold_at
          ,IFNULL(DATE_FORMAT(r.canceled_at, '%Y-%m-%dT%H:%i:%s.%fZ'), '') as canceled_at
      INTO OUTFILE '{}_report.csv'
      FIELDS TERMINATED BY ','
      LINES TERMINATED BY '\n'
      FROM reservations r
      INNER JOIN sheets s
      ON s.id = r.sheet_id
      INNER JOIN events e
      ON e.id = r.event_id
      ORDER BY reserved_at ASC);
    '''.format(prefix))
    reservations = cur.fetchall()

    #reports = []
    #for reservation in reservations:
    #    if reservation['canceled_at']:
    #        canceled_at = reservation['canceled_at'].isoformat()+"Z"
    #    else: canceled_at = ''
    #    reports.append({
    #        "reservation_id": reservation['id'],
    #        "event_id":       reservation['event_id'],
    #        "rank":           reservation['sheet_rank'],
    #        "num":            reservation['sheet_num'],
    #        "user_id":        reservation['user_id'],
    #        "sold_at":        reservation['reserved_at'].isoformat()+"Z",
    #        "canceled_at":    canceled_at,
    #        "price":          reservation['event_price'] + reservation['sheet_price'],
    #    })
    return render_report_csv(reservations, prefix)


if __name__ == "__main__":
    app.run(port=8080, debug=True, threaded=True)
