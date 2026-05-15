import asyncio
import websockets
import json
import math
import random

# --- Game Configuration ---
WORLD_WIDTH = 5000
WORLD_HEIGHT = 5000
BASE_SPEED = 5.0
BASE_RADIUS = 8
INITIAL_LENGTH = 20

TEAMS = {
    0: (255, 0, 0),    # Red
    1: (0, 255, 0),    # Green
    2: (0, 0, 255),    # Blue
    3: (255, 255, 0),  # Yellow
    4: (255, 0, 255),  # Magenta
    5: (0, 255, 255),  # Cyan
}

class Snake:
    def __init__(self, x, y, color, name, team_id, is_player=False, is_friend=False, is_offspring=False):
        self.id = id(self)
        self.x = x
        self.y = y
        self.color = color
        self.name = name
        self.team_id = team_id
        self.is_player = is_player
        self.is_friend = is_friend
        self.is_offspring = is_offspring
        self.is_legacy = False
        self.grief_boost_timer = 0
        # use independent segment lists
        self.segments = [[x, y] for _ in range(INITIAL_LENGTH)]
        self.angle = random.uniform(0, 2 * math.pi)
        self.target_angle = self.angle
        self.points = 0
        self.alive = True
        self.immunity = 0

    @property
    def length(self):
        return len(self.segments)

    @property
    def agility_bonus(self):
        return max(1.0, 1500 / max(30, self.length))

    @property
    def speed(self):
        base = max(3.8, 8.0 - (self.length * 0.015))
        base *= self.agility_bonus
        if self.grief_boost_timer > 0:
            base *= 1.5
            self.grief_boost_timer -= 1
        return base

    @property
    def radius(self):
        return BASE_RADIUS + (self.length * 0.02)

    def move(self, tx=None, ty=None):
        if not self.alive:
            return
        if self.immunity > 0:
            self.immunity -= 1

        if tx is not None and ty is not None:
            self.target_angle = math.atan2(ty - self.y, tx - self.x)

        diff = self.target_angle - self.angle
        while diff > math.pi:
            diff -= 2 * math.pi
        while diff < -math.pi:
            diff += 2 * math.pi

        turn_rate = 0.15 * self.agility_bonus
        self.angle += diff * turn_rate

        self.x += math.cos(self.angle) * self.speed
        self.y += math.sin(self.angle) * self.speed

        self.x = max(0, min(WORLD_WIDTH, self.x))
        self.y = max(0, min(WORLD_HEIGHT, self.y))

        self.segments.insert(0, [self.x, self.y])
        if len(self.segments) > self.length:
            self.segments.pop()

    def grow(self, n=1):
        for _ in range(n):
            self.segments.append(self.segments[-1].copy())

    def to_dict(self):
        return {
            "id": self.id,
            "x": self.x,
            "y": self.y,
            "segments": self.segments,
            "color": f"rgb{self.color}",
            "name": self.name,
            "team_id": self.team_id,
            "points": self.points,
            "isPlayer": self.is_player,
            "isFriend": self.is_friend,
            "isOffspring": self.is_offspring,
            "isLegacy": self.is_legacy,
            "length": self.length
        }

# --- Global State ---
clients = set()
client_players = {}  # websocket -> player_id
snakes = {}
foods = []

def gen_food(n=100):
    for _ in range(n):
        foods.append({
            "x": random.randint(0, WORLD_WIDTH),
            "y": random.randint(0, WORLD_HEIGHT),
            "value": random.randint(1, 3)
        })

def spawn_bot():
    x = random.randint(0, WORLD_WIDTH)
    y = random.randint(0, WORLD_HEIGHT)
    team_id = random.randint(0, 5)
    name = f"Bot_{random.randint(1, 999)}"
    return Snake(x, y, TEAMS[team_id], name, team_id, is_player=False)

def check_coll(s1, s2):
    if not s1.alive or not s2.alive:
        return False

    dist = math.hypot(s1.x - s2.x, s1.y - s2.y)
    if dist < s1.radius + s2.radius:
        diff = s1.length - s2.length
        if abs(diff) > 100:
            if diff > 0:
                s1.alive = False
                s2.immunity = 120
                s2.points += 50
            else:
                s2.alive = False
                s1.immunity = 120
                s1.points += 50
        else:
            s1.alive = False
            s2.alive = False
        return True
    return False

def check_food(s):
    for f in foods[:]:
        if math.hypot(s.x - f["x"], s.y - f["y"]) < s.radius + 5:
            s.grow(f["value"])
            s.points += f["value"] * 10
            foods.remove(f)
            return True
    return False

def handle_bestie_succession(player, snakes_dict):
    # only run if there was a bestie that died
    bots = list(snakes_dict.values())
    current_bestie = next((b for b in bots if b.is_friend), None)

    # if there is still a living bestie, nothing to do
    if current_bestie and current_bestie.alive:
        return False

    # find offspring on same team
    candidates = [
        b for b in bots
        if b.is_offspring and b.alive and b.team_id == player.team_id
    ]
    if not candidates:
        return False

    new_bestie = max(candidates, key=lambda x: len(x.segments))
    new_bestie.is_offspring = False
    new_bestie.is_friend = True
    new_bestie.is_legacy = True
    new_bestie.grief_boost_timer = 600

    old_name = current_bestie.name if current_bestie else "Bestie"
    new_bestie.name = f"{new_bestie.name} (of {old_name})"

    # remove old bestie from snakes if it exists and is dead
    if current_bestie and not current_bestie.alive:
        snakes_dict.pop(current_bestie.id, None)

    return True

async def handler(websocket):
    clients.add(websocket)
    player_id = None
    try:
        async for message in websocket:
            data = json.loads(message)

            if data["type"] == "join":
                team_id = random.randint(0, 5)
                x = random.randint(500, WORLD_WIDTH - 500)
                y = random.randint(500, WORLD_HEIGHT - 500)

                player = Snake(x, y, TEAMS[team_id], data["name"], team_id, is_player=True)
                snakes[player.id] = player
                player_id = player.id
                client_players[websocket] = player.id

                bestie = Snake(x + 50, y, TEAMS[team_id], "Bestie", team_id, is_friend=True)
                snakes[bestie.id] = bestie

                await websocket.send(json.dumps({
                    "type": "joined",
                    "player_id": player.id
                }))

            elif data["type"] == "move":
                pid = data.get("player_id")
                if pid in snakes:
                    s = snakes[pid]
                    s.move(data["targetX"], data["targetY"])
                    check_food(s)
                    handle_bestie_succession(s, snakes)

    finally:
        clients.discard(websocket)
        pid = client_players.pop(websocket, None)
        if pid is not None and pid in snakes:
            snakes.pop(pid, None)

async def game_loop():
    gen_food(200)

    for _ in range(20):
        bot = spawn_bot()
        snakes[bot.id] = bot

    while True:
        for s in list(snakes.values()):
            if s.is_player:
                continue
            if s.alive:
                if random.random() < 0.05:
                    s.target_angle = random.uniform(0, 2 * math.pi)
                s.move()
                check_food(s)

        snake_list = list(snakes.values())
        for i, a in enumerate(snake_list):
            for b in snake_list[i+1:]:
                if a.alive and b.alive:
                    check_coll(a, b)

        # collect dead snakes first
        dead_ids = [s.id for s in snakes.values() if not s.alive]
        for sid in dead_ids:
            s = snakes.pop(sid, None)
            if s:
                for seg in s.segments:
                    foods.append({"x": seg[0], "y": seg[1], "value": 1})

        while len(foods) < 200:
            foods.append({
                "x": random.randint(0, WORLD_WIDTH),
                "y": random.randint(0, WORLD_HEIGHT),
                "value": random.randint(1, 3)
            })

        state = {
            "type": "update",
            "snakes": [s.to_dict() for s in snakes.values() if s.alive],
            "foods": foods[:100]
        }

        if clients:
            msg = json.dumps(state)
            to_remove = []
            for c in clients:
                try:
                    await c.send(msg)
                except:
                    to_remove.append(c)
            for c in to_remove:
                clients.discard(c)
                pid = client_players.pop(c, None)
                if pid is not None:
                    snakes.pop(pid, None)

        await asyncio.sleep(1/30)

async def main():
    print("🐍 SliderSnake Server Started!")
    print("🌐 Open index.html in your browser.")
    print("🔌 Connecting to localhost:8765...")

    async with websockets.serve(handler, "0.0.0.0", 8765):
        await game_loop()

if __name__ == "__main__":
    asyncio.run(main())

