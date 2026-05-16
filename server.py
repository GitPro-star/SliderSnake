import asyncio
import json
import math
import random
import os
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles  # Import StaticFiles
import uvicorn

# --- Configuration ---
WORLD_WIDTH = 5000
WORLD_HEIGHT = 5000
BASE_SPEED = 5.0
BASE_RADIUS = 8
INITIAL_LENGTH = 20
SKINS = ["classic", "neon", "rainbow", "gold", "void"]

TEAMS = {
    0: (255, 0, 0), 1: (0, 255, 0), 2: (0, 0, 255),
    3: (255, 255, 0), 4: (255, 0, 255), 5: (0, 255, 255),
}

class Snake:
    def __init__(self, x, y, color, name, team_id, is_player=False, is_friend=False, is_offspring=False, skin="classic"):
        self.id = id(self)
        self.x, self.y = x, y
        self.color, self.name = color, name
        self.team_id, self.is_player = team_id, is_player
        self.is_friend, self.is_offspring = is_friend, is_offspring
        self.is_legacy, self.grief_boost_timer = False, 0
        self.skin, self.hue = skin, random.randint(0, 360)
        self.segments = [[x, y]] * INITIAL_LENGTH
        self.angle = random.uniform(0, 2 * math.pi)
        self.target_angle = self.angle
        self.points, self.alive, self.immunity = 0, True, 0

    @property
    def length(self): return len(self.segments)
    @property
    def agility_bonus(self): return max(1.0, 1500 / max(30, self.length))
    @property
    def speed(self):
        base = max(3.8, 8.0 - (self.length * 0.015)) * self.agility_bonus
        if self.grief_boost_timer > 0: base *= 1.5; self.grief_boost_timer -= 1
        return base
    @property
    def radius(self): return BASE_RADIUS + (self.length * 0.02)

    def move(self, tx=None, ty=None):
        if not self.alive: return
        if self.immunity > 0: self.immunity -= 1
        if tx is not None and ty is not None: self.target_angle = math.atan2(ty - self.y, tx - self.x)
        diff = self.target_angle - self.angle
        while diff > math.pi: diff -= 2 * math.pi
        while diff < -math.pi: diff += 2 * math.pi
        self.angle += diff * (0.15 * self.agility_bonus)
        self.x += math.cos(self.angle) * self.speed
        self.y += math.sin(self.angle) * self.speed
        self.x = max(0, min(WORLD_WIDTH, self.x))
        self.y = max(0, min(WORLD_HEIGHT, self.y))
        self.segments.insert(0, [self.x, self.y])
        if len(self.segments) > self.length: self.segments.pop()

    def grow(self, n=1):
        for _ in range(n): self.segments.append(self.segments[-1].copy())

    def to_dict(self):
        return {
            "id": self.id, "x": self.x, "y": self.y, "segments": self.segments,
            "color": f"rgb{self.color}", "name": self.name, "team_id": self.team_id,
            "points": self.points, "isPlayer": self.is_player, "isFriend": self.is_friend,
            "isOffspring": self.is_offspring, "isLegacy": self.is_legacy,
            "length": self.length, "skin": self.skin, "hue": self.hue
        }

clients, snakes, foods = [], {}, []

def gen_food(n=100):
    for _ in range(n): foods.append({"x": random.randint(0, WORLD_WIDTH), "y": random.randint(0, WORLD_HEIGHT), "value": random.randint(1, 3)})

def spawn_bot():
    return Snake(random.randint(0, WORLD_WIDTH), random.randint(0, WORLD_HEIGHT), TEAMS[random.randint(0, 5)], f"Bot_{random.randint(1,999)}", random.randint(0, 5), is_player=False, skin="classic")

def check_coll(s1, s2):
    if not s1.alive or not s2.alive: return False
    dist = math.hypot(s1.x - s2.x, s1.y - s2.y)
    if dist < s1.radius + s2.radius:
        diff = s1.length - s2.length
        if abs(diff) > 100:
            if diff > 0: s1.alive = False; s2.immunity = 120; s2.points += 50
            else: s2.alive = False; s1.immunity = 120; s1.points += 50
        else: s1.alive = False; s2.alive = False
        return True
    return False

def check_food(s):
    for f in foods[:]:
        if math.hypot(s.x - f["x"], s.y - f["y"]) < s.radius + 5:
            s.grow(f["value"]); s.points += f["value"] * 10; foods.remove(f); return True
    return False

def handle_bestie_succession(player, bots):
    current_bestie = next((b for b in bots if b.is_friend and b.alive), None)
    if not current_bestie:
        candidates = [b for b in bots if b.is_offspring and b.alive and b.team_id == player.team_id]
        if candidates:
            new_bestie = max(candidates, key=lambda x: len(x.segments))
            new_bestie.is_offspring = False; new_bestie.is_friend = True; new_bestie.is_legacy = True; new_bestie.grief_boost_timer = 600
            new_bestie.name = f"{new_bestie.name} (of {current_bestie.name if current_bestie else 'Unknown'})"
            if current_bestie: bots.remove(current_bestie)
            return True
    return False

# --- FastAPI App ---
app = FastAPI()

# MOUNT STATIC FILES TO SERVE index.html AT ROOT "/"
# This ensures that visiting the main URL loads the game, not the JSON message
app.mount("/", StaticFiles(directory=".", html=True), name="static")

# REMOVED the @app.get("/") function so it doesn't override the static file

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    clients.append(websocket)
    try:
        while True:
            data = await websocket.receive_text()
            data_json = json.loads(data)
            
            if data_json["type"] == "join":
                team_id = random.randint(0, 5)
                x = random.randint(500, WORLD_WIDTH - 500)
                y = random.randint(500, WORLD_HEIGHT - 500)
                skin = data_json.get("skin", "classic")
                if skin not in SKINS: skin = "classic"
                
                player = Snake(x, y, TEAMS[team_id], data_json["name"], team_id, is_player=True, skin=skin)
                snakes[player.id] = player
                
                bestie = Snake(x + 50, y, TEAMS[team_id], "Bestie", team_id, is_friend=True, skin=skin)
                snakes[bestie.id] = bestie
                
                await websocket.send_json({"type": "joined", "player_id": player.id})
            
            elif data_json["type"] == "move":
                if data_json["player_id"] in snakes:
                    s = snakes[data_json["player_id"]]
                    s.move(data_json["targetX"], data_json["targetY"])
                    check_food(s)
                    handle_bestie_succession(s, list(snakes.values()))
    except WebSocketDisconnect:
        clients.remove(websocket)
        if "player_id" in locals() and data_json["player_id"] in snakes:
            del snakes[data_json["player_id"]]

async def game_loop():
    gen_food(200)
    for _ in range(20): snakes[spawn_bot().id] = spawn_bot()
    while True:
        for s in list(snakes.values()):
            if not s.is_player and s.alive:
                if random.random() < 0.05: s.target_angle = random.uniform(0, 2 * math.pi)
                s.move(); check_food(s)
        for s in list(snakes.values()):
            if s.skin == "rainbow": s.hue = (s.hue + 2) % 360
        snake_list = list(snakes.values())
        for i, a in enumerate(snake_list):
            for b in snake_list[i+1:]:
                if a.alive and b.alive: check_coll(a, b)
        for s in list(snakes.values()):
            if not s.alive:
                del snakes[s.id]
                for seg in s.segments: foods.append({"x": seg[0], "y": seg[1], "value": 1})
        while len(foods) < 200: foods.append({"x": random.randint(0, WORLD_WIDTH), "y": random.randint(0, WORLD_HEIGHT), "value": random.randint(1, 3)})
        state = {"type": "update", "snakes": [s.to_dict() for s in snakes.values() if s.alive], "foods": foods[:100]}
        for c in clients:
            try: await c.send_json(state)
            except: pass
        await asyncio.sleep(1/30)

# --- Main Entry Point for Uvicorn ---
if __name__ == "__main__":
    # Start the game loop in a background task
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.create_task(game_loop())
    
    port = int(os.environ.get("PORT", 8000))
    print(f"🐍 SliderSnake Server starting on port {port}...")
    print(f"🌍 Region: Frankfurt (Optimized for Poland/Europe)")
    uvicorn.run(app, host="0.0.0.0", port=port)
