"""Local MySQL-backed OTA contract mock for the Tianyiyou backend."""
from __future__ import annotations

import json
import os
import secrets
import time
import uuid
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

import pymysql
from pymysql.cursors import DictCursor

ROOT = os.path.dirname(os.path.abspath(__file__))
HOST = os.environ.get("OTA_MOCK_HOST", "127.0.0.1")
PORT = int(os.environ.get("OTA_MOCK_PORT", "8787"))
MYSQL_HOST = os.environ.get("OTA_MOCK_MYSQL_HOST", "127.0.0.1")
MYSQL_PORT = int(os.environ.get("OTA_MOCK_MYSQL_PORT", "3306"))
MYSQL_USER = os.environ.get("OTA_MOCK_MYSQL_USER", "root")
MYSQL_PASSWORD = os.environ.get("OTA_MOCK_MYSQL_PASSWORD") or os.environ.get("MYSQL_PWD", "")
MYSQL_DATABASE = os.environ.get("OTA_MOCK_MYSQL_DATABASE", "tyy_ota_mock")
TOKEN = ""


def now():
    return datetime.now(timezone.utc)


def stamp(value):
    return value.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def mysql_datetime(value):
    if not value:
        return None
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


class Database:
    """Small execute-compatible wrapper around a PyMySQL connection."""

    def __init__(self, connection):
        self.connection = connection

    def execute(self, statement, parameters=()):
        cursor = self.connection.cursor()
        cursor.execute(statement, parameters)
        return cursor

    def executescript(self, script):
        for statement in script.split(";"):
            if statement.strip():
                self.execute(statement)

    def close(self):
        self.connection.close()


def raw_connection(database=None):
    options = {
        "host": MYSQL_HOST,
        "port": MYSQL_PORT,
        "user": MYSQL_USER,
        "password": MYSQL_PASSWORD,
        "charset": "utf8mb4",
        "autocommit": True,
        "cursorclass": DictCursor,
        "connect_timeout": 5,
        "read_timeout": 10,
        "write_timeout": 10,
    }
    if database:
        options["database"] = database
    return pymysql.connect(**options)


@contextmanager
def connect():
    connection = raw_connection(MYSQL_DATABASE)
    try:
        yield Database(connection)
    finally:
        connection.close()


def load_token():
    configured = os.environ.get("OTA_MOCK_TOKEN")
    if configured:
        return configured
    key_path = os.path.join(ROOT, "data", "api-key.txt")
    os.makedirs(os.path.dirname(key_path), exist_ok=True)
    if not os.path.exists(key_path):
        with open(key_path, "w", encoding="utf-8") as key_file:
            key_file.write(secrets.token_urlsafe(32) + "\n")
    with open(key_path, "r", encoding="utf-8") as key_file:
        token = key_file.read().strip()
    if len(token) < 32:
        raise RuntimeError("data/api-key.txt must contain at least 32 characters")
    return token


def init_db():
    if not MYSQL_PASSWORD:
        raise RuntimeError("Set OTA_MOCK_MYSQL_PASSWORD before starting the MySQL-backed mock")
    if not MYSQL_DATABASE.replace("_", "").isalnum():
        raise RuntimeError("OTA_MOCK_MYSQL_DATABASE may contain only letters, digits, and underscores")
    server = raw_connection()
    try:
        server.cursor().execute(
            "CREATE DATABASE IF NOT EXISTS `" + MYSQL_DATABASE + "` "
            "CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci"
        )
    finally:
        server.close()
    with connect() as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS offers (
          offer_id VARCHAR(80) PRIMARY KEY,
          business VARCHAR(16) NOT NULL,
          title VARCHAR(255) NOT NULL DEFAULT '',
          flight_number VARCHAR(32) NOT NULL DEFAULT '',
          airline VARCHAR(100) NOT NULL DEFAULT '',
          origin VARCHAR(100) NOT NULL DEFAULT '',
          destination VARCHAR(100) NOT NULL DEFAULT '',
          depart_at VARCHAR(40) NOT NULL DEFAULT '',
          arrive_at VARCHAR(40) NOT NULL DEFAULT '',
          from_airport VARCHAR(100) NOT NULL DEFAULT '',
          to_airport VARCHAR(100) NOT NULL DEFAULT '',
          duration_minutes INT NULL,
          direct TINYINT(1) NOT NULL DEFAULT 1,
          cabin_id VARCHAR(32) NOT NULL DEFAULT '',
          price_cents BIGINT NOT NULL DEFAULT 0,
          tax_cents BIGINT NOT NULL DEFAULT 0,
          total_cents BIGINT NOT NULL DEFAULT 0,
          seats_remaining INT NOT NULL DEFAULT 0,
          currency CHAR(3) NOT NULL DEFAULT 'CNY',
          city VARCHAR(100) NOT NULL DEFAULT '',
          room_name VARCHAR(120) NOT NULL DEFAULT '',
          ticket_type_name VARCHAR(120) NOT NULL DEFAULT '',
          facilities TEXT NOT NULL,
          address VARCHAR(500) NOT NULL DEFAULT '',
          description TEXT NOT NULL,
          refund_policy TEXT NOT NULL,
          baggage TEXT NOT NULL,
          change_policy TEXT NOT NULL,
          expires_at DATETIME(3) NOT NULL,
          payload JSON NOT NULL,
          created_at DATETIME(3) NOT NULL,
          KEY ix_offers_search (business, origin, destination, depart_at),
          KEY ix_offers_expiry (expires_at)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
        CREATE TABLE IF NOT EXISTS bookings (
          supplier_order_id VARCHAR(80) PRIMARY KEY,
          request_id VARCHAR(160) NOT NULL UNIQUE,
          offer_id VARCHAR(80) NOT NULL,
          status VARCHAR(32) NOT NULL,
          amount_cents BIGINT NOT NULL,
          total_cents BIGINT NOT NULL,
          currency CHAR(3) NOT NULL DEFAULT 'CNY',
          expires_at DATETIME(3) NULL,
          passenger_seats INT NOT NULL DEFAULT 1,
          adults INT NOT NULL DEFAULT 1,
          children INT NOT NULL DEFAULT 0,
          infants INT NOT NULL DEFAULT 0,
          ticket_numbers JSON NOT NULL,
          refund_cents BIGINT NULL,
          approved_refund_cents BIGINT NULL,
          payload JSON NOT NULL,
          created_at DATETIME(3) NOT NULL,
          updated_at DATETIME(3) NOT NULL,
          KEY ix_bookings_offer (offer_id),
          KEY ix_bookings_status_updated (status, updated_at),
          CONSTRAINT fk_bookings_offer FOREIGN KEY (offer_id) REFERENCES offers(offer_id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
        CREATE TABLE IF NOT EXISTS events (
          id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
          request_id VARCHAR(160) NULL,
          event VARCHAR(64) NOT NULL,
          detail JSON NOT NULL,
          created_at DATETIME(3) NOT NULL,
          KEY ix_events_request (request_id),
          KEY ix_events_created (created_at)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
        """)


def record(db, request_id, event, detail=None):
    db.execute(
        "INSERT INTO events(request_id,event,detail,created_at) VALUES(%s,%s,%s,%s)",
        (request_id, event, json.dumps(detail or {}, ensure_ascii=False), mysql_datetime(stamp(now()))),
    )


def airport_name(value, direction):
    names = {
        "HAK": "海口美兰", "PEK": "北京首都", "SHA": "上海虹桥",
        "PVG": "上海浦东", "CAN": "广州白云", "SZX": "深圳宝安",
        "CTU": "成都双流", "HGH": "杭州萧山", "SHE": "沈阳桃仙",
        "海口": "海口美兰", "北京": "北京首都", "上海": "上海虹桥",
        "广州": "广州白云", "深圳": "深圳宝安", "成都": "成都双流",
        "杭州": "杭州萧山", "沈阳": "沈阳桃仙", "三亚": "三亚凤凰",
    }
    value = str(value or "")
    if value in names:
        return names[value]
    if value and not value.isupper():
        return value + ("机场" if value.endswith(("机场", "国际机场")) else "美兰" if value == "海口" else "机场")
    return value or ("海口美兰" if direction == "from" else "上海浦东")


def make_offer(business, query=None, cabin_index=0, scenario=""):
    query = query or {}
    oid = "MO-" + uuid.uuid4().hex[:16]
    exp = stamp(now() + timedelta(minutes=20))
    if business == "FLIGHT":
        number = query.get("flightNumber") or ("HU7611" if cabin_index == 0 else "HU7613")
        cabins = (("ECONOMY", 68000, 5000), ("BUSINESS", 188000, 5000))
        cabin, price, tax = cabins[cabin_index % len(cabins)]
        cabin = str(query.get("cabinId") or cabin).upper()
        if cabin == "BUSINESS":
            price = 188000
        elif cabin == "ECONOMY":
            price = 68000
        seats = 0 if scenario == "SOLD_OUT" else max(0, int(query.get("seatsRemaining", 6)))
        origin = query.get("origin", "HAK")
        destination = query.get("destination", "PVG")
        depart_at = query.get("date", "") + "T10:20:00+08:00"
        arrive_at = query.get("date", "") + "T13:10:00+08:00"
        policy = "模拟数据：退改规则以实际供应商为准"
        cabin_item = {"id": cabin, "name": "商务舱" if cabin == "BUSINESS" else "经济舱",
                      "priceCents": price, "taxCents": tax, "baggage": "免费托运行李 20kg（模拟）",
                      "refundPolicy": policy, "changePolicy": "模拟数据：改签规则以实际供应商为准"}
        payload = {"offerId": oid, "business": business, "title": "海南航空 " + number,
          "flightNumber": number, "airline": "海南航空", "origin": origin, "destination": destination,
          "departAt": depart_at, "arriveAt": arrive_at,
          "departureAt": depart_at, "arrivalAt": arrive_at,
          "fromAirport": airport_name(query.get("fromAirport") or origin, "from"),
          "toAirport": airport_name(query.get("toAirport") or destination, "to"),
          "durationMinutes": 170, "direct": True, "cabinId": cabin, "cabins": [cabin_item],
          "priceCents": price, "taxCents": tax, "totalCents": price + tax,
          "seatsRemaining": seats, "currency": "CNY", "refundPolicy": policy, "expiresAt": exp}
    elif business == "HOTEL":
        payload = {"offerId": oid, "business": business, "title": "模拟海景酒店·高级大床房",
          "city": query.get("city", "三亚"), "roomName": "高级大床房", "priceCents": 42800,
          "totalCents": 42800, "currency": "CNY", "expiresAt": exp,
          "refundPolicy": "模拟数据：入住前可申请取消"}
    else:
        payload = {"offerId": oid, "business": business, "title": "模拟景区成人票",
          "ticketTypeName": "成人票", "priceCents": 12800, "totalCents": 12800,
          "currency": "CNY", "expiresAt": exp, "refundPolicy": "模拟数据：未使用可申请退款"}
    return payload


FLIGHT_SCHEDULES = (
    {"airline": "海南航空", "number": "HU7611", "depart": "06:45", "duration": 175, "price": 59800, "seats": 6, "direct": True, "from": "海口美兰", "to": "上海浦东"},
    {"airline": "东方航空", "number": "MU5131", "depart": "08:20", "duration": 170, "price": 67200, "seats": 3, "direct": True, "from": "海口美兰", "to": "上海虹桥"},
    {"airline": "南方航空", "number": "CZ6789", "depart": "10:55", "duration": 195, "price": 73500, "seats": 12, "direct": True, "from": "海口美兰", "to": "上海浦东"},
    {"airline": "四川航空", "number": "3U3874", "depart": "13:25", "duration": 285, "price": 51800, "seats": 8, "direct": False, "from": "海口美兰", "to": "上海虹桥", "stop": "广州白云"},
    {"airline": "深圳航空", "number": "ZH9912", "depart": "15:50", "duration": 180, "price": 82400, "seats": 2, "direct": True, "from": "海口美兰", "to": "上海浦东"},
    {"airline": "厦门航空", "number": "MF8346", "depart": "18:35", "duration": 250, "price": 56300, "seats": 9, "direct": False, "from": "海口美兰", "to": "上海浦东", "stop": "厦门高崎"},
    {"airline": "中国国航", "number": "CA1388", "depart": "21:10", "duration": 175, "price": 91800, "seats": 4, "direct": True, "from": "海口美兰", "to": "上海虹桥"},
    {"airline": "海南航空", "number": "HU7612", "depart": "07:10", "duration": 180, "price": 61200, "seats": 5, "direct": True, "from": "上海浦东", "to": "海口美兰"},
    {"airline": "东方航空", "number": "MU5132", "depart": "09:05", "duration": 175, "price": 68400, "seats": 4, "direct": True, "from": "上海虹桥", "to": "海口美兰"},
    {"airline": "南方航空", "number": "CZ6790", "depart": "11:30", "duration": 200, "price": 74800, "seats": 10, "direct": True, "from": "上海浦东", "to": "海口美兰"},
    {"airline": "四川航空", "number": "3U3875", "depart": "13:40", "duration": 295, "price": 52600, "seats": 7, "direct": False, "from": "上海虹桥", "to": "海口美兰", "stop": "广州白云"},
    {"airline": "深圳航空", "number": "ZH9913", "depart": "16:15", "duration": 185, "price": 83600, "seats": 3, "direct": True, "from": "上海浦东", "to": "海口美兰"},
    {"airline": "厦门航空", "number": "MF8347", "depart": "18:50", "duration": 260, "price": 57100, "seats": 8, "direct": False, "from": "上海浦东", "to": "海口美兰", "stop": "厦门高崎"},
    {"airline": "中国国航", "number": "CA1389", "depart": "20:35", "duration": 180, "price": 92700, "seats": 4, "direct": True, "from": "上海虹桥", "to": "海口美兰"},
)

FLIGHT_TEST_HORIZON_DAYS = 5
FLIGHT_TEST_DAILY_LIMIT = 5


def route_group(value):
    value = str(value or "").strip().upper()
    if value in ("HAK", "海口", "海口美兰"):
        return "HAK"
    if value in ("SHA", "PVG", "上海", "上海虹桥", "上海浦东"):
        return "SHA"
    return value


def flight_offer(query, schedule, index, scenario=""):
    service_date = date.fromisoformat(str(query.get("date", "")))
    origin = str(query.get("origin", "HAK"))
    destination = str(query.get("destination", "PVG"))
    cabin = str(query.get("cabin") or query.get("cabinId") or "ECONOMY").upper()
    if cabin not in ("ECONOMY", "BUSINESS"):
        cabin = "ECONOMY"
    tax = 5000
    economy = int(schedule["price"])
    if cabin == "BUSINESS":
        price = int(178000 + index * 17600 + (service_date.toordinal() % 5) * 1300)
        cabin_name = "商务舱"
        baggage = "免费托运行李 30kg（模拟）"
        refund = "模拟商务舱规则：起飞前可按规则申请退票，可能收取退票费"
    else:
        price = economy + (service_date.toordinal() % 4) * 900
        cabin_name = "经济舱"
        baggage = "免费托运行李 20kg（模拟）"
        refund = "模拟经济舱规则：按票价舱位收取退票费，特价舱可能不得退票"

    seats = 0 if scenario == "SOLD_OUT" else int(schedule["seats"])
    depart_local = datetime.combine(service_date, datetime.strptime(schedule["depart"], "%H:%M").time())
    depart_at = depart_local.isoformat(timespec="seconds") + "+08:00"
    arrival_local = depart_local + timedelta(minutes=int(schedule["duration"]))
    arrive_at = arrival_local.isoformat(timespec="seconds") + "+08:00"
    stop = schedule.get("stop", "")
    route_from = airport_name(origin, "from")
    route_to = airport_name(destination, "to")
    route_label = "经" + stop + "中转" if stop else "直飞"
    change = "模拟数据：中转航班需按联程规则办理改签" if stop else "模拟数据：改签规则按所选舱位执行"
    cabin_item = {"id": cabin, "name": cabin_name, "priceCents": price, "taxCents": tax,
                  "baggage": baggage, "refundPolicy": refund, "changePolicy": change}
    return {
        "offerId": "MO-" + uuid.uuid4().hex[:16], "business": "FLIGHT",
        "title": schedule["airline"] + " " + schedule["number"],
        "flightNumber": schedule["number"], "airline": schedule["airline"],
        "origin": origin, "destination": destination,
        "departAt": depart_at, "arriveAt": arrive_at,
        "departureAt": depart_at, "arrivalAt": arrive_at,
        "fromAirport": schedule["from"],
        "toAirport": schedule["to"],
        "durationMinutes": int(schedule["duration"]), "direct": bool(schedule["direct"]),
        "stops": [stop] if stop else [], "routeLabel": route_label,
        "cabinId": cabin, "cabins": [cabin_item], "priceCents": price,
        "taxCents": tax, "totalCents": price + tax, "seatsRemaining": seats,
        "currency": "CNY", "refundPolicy": refund,
        "expiresAt": stamp(now() + timedelta(minutes=20)),
    }


def search_flights(query, scenario=""):
    try:
        service_date = date.fromisoformat(str(query.get("date", "")))
    except ValueError:
        print("invalid flight search fields: %s" % sorted(query.keys()), flush=True)
        raise ValueError("date must use YYYY-MM-DD format; received %r" % query.get("date"))
    try:
        seats_needed = int(query.get("adults", 1)) + int(query.get("children", 0))
    except (TypeError, ValueError):
        raise ValueError("passenger count must be an integer")
    if seats_needed < 1:
        raise ValueError("at least one adult or child is required")

    requested_airline = str(query.get("airline", "")).strip().casefold()
    after_time = str(query.get("departAfter", "")).strip()
    before_time = str(query.get("departBefore", "")).strip()
    for value in (after_time, before_time):
        if value:
            try:
                datetime.strptime(value, "%H:%M")
            except ValueError:
                raise ValueError("departure time filters must use HH:MM format")

    flights = []
    current_local = now().astimezone(timezone(timedelta(hours=8)))
    if service_date < current_local.date() or service_date > current_local.date() + timedelta(days=FLIGHT_TEST_HORIZON_DAYS):
        return flights

    origin_group = route_group(query.get("origin"))
    destination_group = route_group(query.get("destination"))
    route_schedules = []
    for index, schedule in enumerate(FLIGHT_SCHEDULES):
        schedule_origin = "HAK" if schedule["from"].startswith("海口") else "SHA"
        schedule_destination = "HAK" if schedule["to"].startswith("海口") else "SHA"
        if (origin_group, destination_group) == (schedule_origin, schedule_destination):
            route_schedules.append((index, schedule))

    for index, schedule in route_schedules[:FLIGHT_TEST_DAILY_LIMIT]:
        depart_clock = schedule["depart"]
        if after_time and depart_clock < after_time:
            continue
        if before_time and depart_clock > before_time:
            continue
        if str(query.get("direct", "")).lower() in ("1", "true", "yes") and not schedule["direct"]:
            continue
        if requested_airline and requested_airline not in schedule["airline"].casefold() and requested_airline not in schedule["number"].casefold():
            continue
        if service_date == current_local.date() and schedule["depart"] <= current_local.strftime("%H:%M"):
            continue
        if scenario != "SOLD_OUT" and int(schedule["seats"]) < seats_needed:
            continue
        flights.append(flight_offer(query, schedule, index, scenario))

    sort_key = str(query.get("sort", "price_asc")).lower()
    if sort_key == "departure":
        flights.sort(key=lambda item: item["departAt"])
    elif sort_key == "duration":
        flights.sort(key=lambda item: (item["durationMinutes"], item["totalCents"]))
    else:
        flights.sort(key=lambda item: (item["totalCents"], item["departAt"]))
    return flights


def offer_columns(offer):
    return (offer.get("offerId", ""), offer.get("business", ""), offer.get("title", ""),
      offer.get("flightNumber", ""), offer.get("airline", ""), offer.get("origin", ""),
      offer.get("destination", ""), offer.get("departAt", offer.get("departureAt", "")),
      offer.get("arriveAt", offer.get("arrivalAt", "")), offer.get("fromAirport", ""),
      offer.get("toAirport", ""), offer.get("durationMinutes"), int(bool(offer.get("direct", True))),
      offer.get("cabinId", ""), int(offer.get("priceCents", 0)), int(offer.get("taxCents", 0)),
      int(offer.get("totalCents", offer.get("priceCents", 0))), int(offer.get("seatsRemaining", 0)),
      offer.get("currency", "CNY"), offer.get("city", ""), offer.get("roomName", ""),
      offer.get("ticketTypeName", ""), offer.get("facilities", ""), offer.get("address", ""),
      offer.get("description", ""), offer.get("refundPolicy", ""),
      (offer.get("cabins") or [{}])[0].get("baggage", ""),
      (offer.get("cabins") or [{}])[0].get("changePolicy", ""),
      mysql_datetime(offer.get("expiresAt")), json.dumps(offer, ensure_ascii=False), mysql_datetime(stamp(now())))


OFFER_COLUMNS = "offer_id,business,title,flight_number,airline,origin,destination,depart_at,arrive_at,from_airport,to_airport,duration_minutes,direct,cabin_id,price_cents,tax_cents,total_cents,seats_remaining,currency,city,room_name,ticket_type_name,facilities,address,description,refund_policy,baggage,change_policy,expires_at,payload,created_at"
OFFER_PLACEHOLDERS = ",".join(["%s"] * 31)


def save_offer(db, offer):
    columns = offer_columns(offer)
    db.execute("INSERT INTO offers(" + OFFER_COLUMNS + ") VALUES(" + OFFER_PLACEHOLDERS + ")", columns)


def update_offer(db, offer):
    columns = offer_columns(offer)
    db.execute("UPDATE offers SET seats_remaining=%s,payload=%s WHERE offer_id=%s",
               (columns[17], columns[29], offer["offerId"]))


def save_booking(db, offer_id, request_id, result, internal_supplier_id=None):
    summary = result.get("passengerSummary") or {}
    supplier_id = internal_supplier_id or result.get("supplierOrderId")
    amount = int(result.get("amountCents", result.get("totalCents", 0)))
    total = int(result.get("totalCents", amount))
    ticket_numbers = result.get("ticketNumbers") or []
    now_value = mysql_datetime(stamp(now()))
    db.execute("""INSERT INTO bookings(
        supplier_order_id,request_id,offer_id,status,amount_cents,total_cents,currency,expires_at,
        passenger_seats,adults,children,infants,ticket_numbers,refund_cents,approved_refund_cents,
        payload,created_at,updated_at) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
        (supplier_id, request_id, offer_id, result.get("status", "RESERVED"), amount, total,
         result.get("currency", "CNY"), mysql_datetime(result.get("expiresAt")),
         int(result.get("passengerSeats", 1)), int(summary.get("adults", 1)),
         int(summary.get("children", 0)), int(summary.get("infants", 0)),
         json.dumps(ticket_numbers, ensure_ascii=False), result.get("refundCents"),
         result.get("approvedRefundCents"), json.dumps(result, ensure_ascii=False), now_value, now_value))


def update_booking(db, supplier_id, request_id, result):
    db.execute("""UPDATE bookings SET status=%s,amount_cents=%s,total_cents=%s,currency=%s,
        expires_at=%s,passenger_seats=%s,adults=%s,children=%s,infants=%s,ticket_numbers=%s,
        refund_cents=%s,approved_refund_cents=%s,payload=%s,updated_at=%s WHERE supplier_order_id=%s""",
        (result.get("status", "RESERVED"), int(result.get("amountCents", result.get("totalCents", 0))),
         int(result.get("totalCents", result.get("amountCents", 0))), result.get("currency", "CNY"),
         mysql_datetime(result.get("expiresAt")), int(result.get("passengerSeats", 1)),
         int((result.get("passengerSummary") or {}).get("adults", 1)),
         int((result.get("passengerSummary") or {}).get("children", 0)),
         int((result.get("passengerSummary") or {}).get("infants", 0)),
         json.dumps(result.get("ticketNumbers") or [], ensure_ascii=False), result.get("refundCents"),
         result.get("approvedRefundCents"), json.dumps(result, ensure_ascii=False),
         mysql_datetime(stamp(now())), supplier_id))
    record(db, request_id, "BOOKING_STATUS_UPDATED", {"supplierOrderId": supplier_id, "status": result.get("status")})


class Handler(BaseHTTPRequestHandler):
    server_version = "TYY-OTA-Mock/1.0"

    def log_message(self, fmt, *args):
        print("[%s] %s" % (self.log_date_time_string(), fmt % args), flush=True)

    def send_json(self, code, value):
        body = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def body(self):
        encodings = [item.strip().lower() for item in self.headers.get("Transfer-Encoding", "").split(",") if item.strip()]
        if encodings and encodings[-1] == "chunked":
            chunks = []
            total = 0
            while True:
                line = self.rfile.readline(8192)
                if not line:
                    raise ValueError("incomplete chunked request body")
                try:
                    size = int(line.split(b";", 1)[0].strip(), 16)
                except ValueError as exc:
                    raise ValueError("invalid chunked request body") from exc
                if size < 0 or total + size > 1_000_000:
                    raise ValueError("request body too large")
                if size == 0:
                    while True:
                        trailer = self.rfile.readline(8192)
                        if trailer in (b"\r\n", b"\n", b""):
                            break
                    break
                chunk = self.rfile.read(size)
                if len(chunk) != size or self.rfile.read(2) != b"\r\n":
                    raise ValueError("incomplete chunked request body")
                chunks.append(chunk)
                total += size
            raw = b"".join(chunks)
        else:
            length = int(self.headers.get("Content-Length", "0"))
            if length > 1_000_000:
                raise ValueError("request body too large")
            raw = self.rfile.read(length) if length else b"{}"
        value = json.loads(raw.decode("utf-8"))
        if not isinstance(value, dict):
            raise ValueError("JSON object required")
        return value

    def dispatch(self):
        if self.path.startswith("/health"):
            with connect() as db:
                count = db.execute("SELECT COUNT(*) AS total FROM bookings").fetchone()["total"]
                return 200, {"ok": True, "service": "tyy-ota-mock", "database": "mysql",
                             "databaseName": MYSQL_DATABASE, "bookings": count}
        if self.headers.get("Authorization") != "Bearer " + TOKEN:
            return 401, {"code": "UNAUTHORIZED", "message": "Invalid mock API key"}
        path = unquote(urlparse(self.path).path)
        scenario = (self.headers.get("X-Mock-Scenario") or os.environ.get("OTA_MOCK_SCENARIO", "")).upper()
        if scenario == "HTTP_503":
            return 503, {"code": "MOCK_UNAVAILABLE", "message": "Injected temporary provider failure"}
        if scenario == "HTTP_429":
            return 429, {"code": "MOCK_RATE_LIMIT", "message": "Injected provider rate limit"}
        if scenario == "TIMEOUT":
            time.sleep(max(31, int(os.environ.get("OTA_MOCK_TIMEOUT_DELAY_SECONDS", "35"))))
        if scenario == "BUSINESS_ERROR":
            return 422, {"code": "MOCK_BUSINESS_ERROR", "message": "Injected supplier business error"}
        with connect() as db:
            if self.command == "GET" and path == "/offers":
                params = parse_qs(urlparse(self.path).query)
                business = str((params.get("business") or [""])[0]).upper()
                service_date = str((params.get("date") or [""])[0])
                if business not in ("FLIGHT", "HOTEL", "TICKET"):
                    return 400, {"code": "INVALID_BUSINESS", "message": "business must be FLIGHT, HOTEL, or TICKET"}
                try:
                    date.fromisoformat(service_date)
                except ValueError:
                    return 400, {"code": "INVALID_DATE", "message": "date must use YYYY-MM-DD format"}
                rows = db.execute(
                    "SELECT payload FROM offers WHERE business=%s AND depart_at LIKE %s ORDER BY depart_at,offer_id",
                    (business, service_date + "%"),
                ).fetchall()
                items = [json.loads(row["payload"]) if isinstance(row["payload"], str) else row["payload"] for row in rows]
                return 200, {"items": items, "total": len(items), "business": business, "date": service_date}
            if self.command == "POST" and path == "/offers/search":
                data = self.body(); business = str(data.get("business", "FLIGHT")).upper()
                if business not in ("FLIGHT", "HOTEL", "TICKET"):
                    return 400, {"code": "INVALID_BUSINESS", "message": "business must be FLIGHT, HOTEL, or TICKET"}
                if scenario == "NO_RESULTS":
                    record(db, None, "OFFERS_SEARCH", {"business": business, "count": 0})
                    return 200, {"items": [], "total": 0}
                if business == "FLIGHT":
                    items = search_flights(data, scenario)
                else:
                    items = [make_offer(business, data, 0, scenario)]
                for offer in items:
                    save_offer(db, offer)
                record(db, None, "OFFERS_SEARCH", {"business": business, "count": len(items),
                                                     "date": data.get("date"), "origin": data.get("origin"),
                                                     "destination": data.get("destination"), "sort": data.get("sort")})
                return 200, {"items": items, "total": len(items)}
            if path.startswith("/offers/"):
                parts = path.strip("/").split("/")
                if len(parts) == 2 and self.command == "GET":
                    row = db.execute("SELECT payload FROM offers WHERE offer_id=%s", (parts[1],)).fetchone()
                    if not row: return 404, {"code": "OFFER_NOT_FOUND", "message": "Offer not found"}
                    return 200, json.loads(row["payload"])
                if len(parts) == 3 and parts[2] == "reprice" and self.command == "POST":
                    row = db.execute("SELECT payload FROM offers WHERE offer_id=%s", (parts[1],)).fetchone()
                    if not row: return 404, {"code": "OFFER_NOT_FOUND", "message": "Offer not found"}
                    offer = json.loads(row["payload"])
                    if scenario == "PRICE_CHANGED":
                        offer["totalCents"] = int(offer.get("totalCents", offer["priceCents"])) + 1200
                    offer["expiresAt"] = stamp(now() + timedelta(minutes=10))
                    offer["cabins"] = offer.get("cabins", [])
                    db.execute("UPDATE offers SET expires_at=%s,payload=%s WHERE offer_id=%s",
                               (mysql_datetime(offer["expiresAt"]), json.dumps(offer, ensure_ascii=False), parts[1]))
                    record(db, None, "OFFER_REPRICE", {"offerId": parts[1], "changed": scenario == "PRICE_CHANGED"})
                    return 200, offer
            if self.command == "POST" and path == "/bookings":
                data = self.body(); req = str(data.get("requestId", ""))
                if not req: return 400, {"code": "REQUEST_ID_REQUIRED", "message": "requestId is required"}
                old = db.execute("SELECT payload FROM bookings WHERE request_id=%s", (req,)).fetchone()
                if old:
                    record(db, req, "IDEMPOTENT_REPLAY")
                    return 200, json.loads(old["payload"])
                offer_row = db.execute("SELECT payload FROM offers WHERE offer_id=%s", (data.get("offerId"),)).fetchone()
                if not offer_row: return 404, {"code": "OFFER_NOT_FOUND", "message": "Offer not found"}
                offer = json.loads(offer_row["payload"]); amount = int(offer.get("totalCents", offer["priceCents"]))
                selection = data.get("selection") if isinstance(data.get("selection"), dict) else {}
                adults = int(selection.get("adults", 1)); children = int(selection.get("children", 0)); infants = int(selection.get("infants", 0))
                seats = max(1, adults + children)
                remaining = int(offer.get("seatsRemaining", 6))
                if scenario == "SOLD_OUT" or remaining < seats:
                    return 409, {"code": "SOLD_OUT", "message": "No seats remain for this mock offer"}
                if scenario == "REJECT_BOOKING":
                    return 409, {"code": "BOOKING_REJECTED", "message": "Injected booking rejection"}
                if scenario == "PRICE_CHANGED": amount += 1200
                supplier_id = "SUP-" + uuid.uuid4().hex[:14].upper()
                result = {"supplierOrderId": supplier_id, "status": "RESERVED", "totalCents": amount,
                          "amountCents": amount, "currency": "CNY", "expiresAt": stamp(now()+timedelta(minutes=15)),
                          "passengerSeats": seats, "passengerSummary": {"adults": adults, "children": children, "infants": infants}}
                if scenario == "MISSING_SUPPLIER_ID": result.pop("supplierOrderId")
                offer["seatsRemaining"] = remaining - seats
                update_offer(db, offer)
                save_booking(db, data.get("offerId", ""), req, result,
                             supplier_id if "supplierOrderId" not in result else None)
                record(db, req, "BOOKING_CREATED", {"status": result["status"]})
                return 200, result
            if path.startswith("/bookings/"):
                parts = path.strip("/").split("/")
                if len(parts) == 3 and parts[1] == "by-request" and self.command == "GET":
                    row = db.execute("SELECT payload FROM bookings WHERE request_id=%s", (parts[2],)).fetchone()
                    if not row: return 404, {"code": "BOOKING_NOT_FOUND", "message": "Request not found"}
                    return 200, json.loads(row["payload"])
                if len(parts) < 2: return 404, {"code": "NOT_FOUND", "message": "Unknown endpoint"}
                supplier = parts[1]
                row = db.execute("SELECT * FROM bookings WHERE supplier_order_id=%s", (supplier,)).fetchone()
                if not row: return 404, {"code": "BOOKING_NOT_FOUND", "message": "Booking not found"}
                if len(parts) == 2 and self.command == "GET": return 200, json.loads(row["payload"])
                if len(parts) == 3 and self.command == "POST":
                    data = self.body(); req = str(data.get("requestId", "")); action = parts[2]
                    if action == "confirm":
                        state = "ISSUED" if row["status"] != "CANCELLED" else row["status"]
                        result = json.loads(row["payload"]); result.update({"status": state, "ticketNumbers": ["MO-TKT-"+supplier[-8:]] if state == "ISSUED" else []})
                    elif action == "cancel":
                        result = json.loads(row["payload"]); result.update({"status": "CANCELLED", "refundCents": row["amount_cents"]})
                    elif action == "refund":
                        result = json.loads(row["payload"]); result.update({"status": "REFUND_APPROVED", "approvedRefundCents": int(data.get("amountCents", row["amount_cents"]))})
                    else: return 404, {"code": "NOT_FOUND", "message": "Unknown booking action"}
                    if scenario == "REFUND_PENDING" and action == "refund": result["status"] = "REFUND_PENDING"
                    terminal = {"CANCELLED", "REFUND_APPROVED"}
                    if result["status"] in terminal and row["status"] not in terminal:
                        offer_row = db.execute("SELECT payload FROM offers WHERE offer_id=%s", (row["offer_id"],)).fetchone()
                        if offer_row:
                            offer = json.loads(offer_row["payload"])
                            offer["seatsRemaining"] = int(offer.get("seatsRemaining", 0)) + int(row["passenger_seats"])
                            update_offer(db, offer)
                    update_booking(db, supplier, req, result)
                    record(db, req, "BOOKING_"+action.upper(), {"supplierOrderId": supplier, "status": result["status"]})
                    return 200, result
        return 404, {"code": "NOT_FOUND", "message": "Unknown endpoint"}

    def _handle(self):
        try:
            code, result = self.dispatch()
        except (ValueError, json.JSONDecodeError) as exc:
            code, result = 400, {"code": "INVALID_REQUEST", "message": str(exc)}
            print("invalid request %s: %s" % (self.path, exc), flush=True)
        except pymysql.MySQLError as exc:
            code, result = 500, {"code": "DATABASE_ERROR", "message": "Mock database operation failed"}
            print("database error:", exc, flush=True)
        except Exception as exc:
            code, result = 500, {"code": "INTERNAL_ERROR", "message": "Mock request failed"}
            print("mock error:", exc, flush=True)
        try:
            self.send_json(code, result)
        except (BrokenPipeError, ConnectionResetError):
            pass

    do_GET = _handle
    do_POST = _handle


if __name__ == "__main__":
    TOKEN = load_token()
    init_db()
    print("Tianyiyou OTA mock listening on http://%s:%s (MySQL: %s:%s/%s)" %
          (HOST, PORT, MYSQL_HOST, MYSQL_PORT, MYSQL_DATABASE), flush=True)
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
