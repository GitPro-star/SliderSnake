import asyncio
import json
import math
import random
import os
from contextlib import asynccontextmanager
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
import uvicorn

# --- Zaawansowana Konfiguracja Świata ---
WORLD_WIDTH = 5000
WORLD_HEIGHT = 5000
BASE_SPEED = 4.0
BASE_RADIUS = 8
INITIAL_LENGTH = 20
MAX_BOTS = 25
MAX_FOOD = 300
SKINS = ["classic", "neon", "rainbow", "gold", "void"]

TEAMS = {
    0: (255, 40, 40),    # Czerwoni
    1: (40, 255, 40),    # Zieloni
    2: (40, 40, 255),    # Niebiescy
    3: (255, 255, 40),   # Żółci
    4: (255, 40, 255),   # Różowi
    5: (40, 255, 255),   # Błękitni
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
        self.points, self.alive, self.immunity = 0, True, 60
        
        # Nowe zaawansowane parametry mechanik:
        self.level = 1
        self.is_sprinting = False
        self.energy_to_lay_egg = 0
        self.poison_cooldown = 0
        self.ai_state = "wander" # wander, hunt, flee (dla botów)

    @property
    def length(self): 
        return len(self.segments)
        
    @property
    def agility_bonus(self): 
        # Mechanika Underdog: mniejsze węże są o wiele bardziej zwrotne
        return max(1.2, 1800 / max(25, self.length))
        
    @property
    def speed(self):
        base = max(3.5, 7.5 - (self.length * 0.012)) * self.agility_bonus
        if self.is_sprinting and self.length > 10:
            base *= 1.6
        if self.grief_boost_timer > 0: 
            base *= 1.5
            self.grief_boost_timer -= 1
        return base
        
    @property
    def radius(self): 
        return BASE_RADIUS + (self.length * 0.025)

    def move(self):
        if not self.alive: return
        if self.immunity > 0: self.immunity -= 1
        if self.poison_cooldown > 0: self.poison_cooldown -= 1
        
        # Koszt energetyczny sprintu
        if self.is_sprinting and self.length > 12:
            if random.random() < 0.15:
                # Tracenie segmentów na rzecz pozostawianego jedzenia
                lost_seg = self.segments.pop()
                lx, ly = lost_seg
                foods.append({"x": lx, "y": ly, "value": 1, "is_poison": False})
        
        # Płynny obrót w kierunku target_angle
        diff = self.target_angle - self.angle
        while diff > math.pi: diff -= 2 * math.pi
        while diff < -math.pi: diff += 2 * math.pi
        self.angle += diff * (0.16 * self.agility_bonus)
        
        self.x += math.cos(self.angle) * self.speed
        self.y += math.sin(self.angle) * self.speed
        
        # Granice areny
        self.x = max(10, min(WORLD_WIDTH - 10, self.x))
        self.y = max(10, min(WORLD_HEIGHT - 10, self.y))
        
        self.segments.insert(0, [self.x, self.y])
        if len(self.segments) > self.length: 
            self.segments.pop()

    def grow(self, n=1):
        for _ in range(n): 
            self.segments.append(self.segments[-1].copy())
        self.points += n * 15
        self.energy_to_lay_egg += n
        self.level = 1 + (self.length // 15)

    def to_dict(self):
        return {
            "id": self.id, "x": self.x, "y": self.y, "segments": self.segments,
            "color": f"rgb{self.color}", "name": self.name, "team_id": self.team_id,
            "points": self.points, "isPlayer": self.is_player, "isFriend": self.is_friend,
            "isOffspring": self.is_offspring, "isLegacy": self.is_legacy,
            "length": self.length, "skin": self.skin, "hue": self.hue,
            "level": self.level, "isSprinting": self.is_sprinting
        }

# --- Struktury Globalne ---
clients, snakes, foods, eggs = [], {}, [], []

def gen_food(n=100):
    for _ in range(n): 
        foods.append({
            "x": random.randint(30, WORLD_WIDTH - 30), 
            "y": random.randint(30, WORLD_HEIGHT - 30), 
            "value": random.randint(1, 4),
            "is_poison": False
        })

def spawn_bot():
    t_id = random.randint(0, 5)
    return Snake(
        random.randint(200, WORLD_WIDTH - 200), 
        random.randint(200, WORLD_HEIGHT - 200), 
        TEAMS[t_id], f"Bot_{random.randint(1,999)}", t_id, 
        is_player=False, skin=random.choice(SKINS)
    )

# --- Zaawansowana Sztuczna Inteligencja Botów (Wyszukiwanie celi, Ucieczka, Atak) ---
def update_bot_ai(bot):
    if not bot.alive or bot.is_player: return
    
    closest_target = None
    closest_dist = 999999
    
    # 1. Sprawdzenie zagrożeń (Ucieczka przed kolosalnymi wężami z innej drużyny)
    for s in snakes.values():
        if s.alive and s.team_id != bot.team_id and s.length > bot.length + 30:
            d = math.hypot(bot.x - s.x, bot.y - s.y)
            if d < 350 and d < closest_dist:
                closest_dist = d
                closest_target = s
                bot.ai_state = "flee"

    # 2. Polowanie (Atakowanie mniejszych wężów, jeśli brak bezpośredniego zagrożenia)
    if bot.ai_state != "flee":
        for s in snakes.values():
            if s.alive and s.team_id != bot.team_id and s.length < bot.length - 10:
                d = math.hypot(bot.x - s.x, bot.y - s.y)
                if d < 400 and d < closest_dist:
                    closest_dist = d
                    closest_target = s
                    bot.ai_state = "hunt"

    # 3. Szukanie jedzenia, gdy naokoło jest spokojnie
    if bot.ai_state == "wander" or closest_target is None:
        bot.ai_state = "wander"
        for f in foods:
            d = math.hypot(bot.x - f["x"], bot.y - f["y"])
            if d < closest_dist:
                closest_dist = d
                closest_target = f

    # Wyliczanie kąta na podstawie podjętej decyzji AI
    if closest_target:
        if isinstance(closest_target, dict): # Cele typu słownik (jedzenie)
            bot.target_angle = math.atan2(closest_target["y"] - bot.y, closest_target["x"] - bot.x)
            bot.is_sprinting = False
        else: # Cele typu obiekt klasy Snake (gracze/boty)
            if bot.ai_state == "flee":
                # Kąt odwrotny od niebezpieczeństwa
                bot.target_angle = math.atan2(bot.y - closest_target.y, bot.x - closest_target.x)
                bot.is_sprinting = True
            elif bot.ai_state == "hunt":
                # Atakowanie głową na odcięcie drogi
                bot.target_angle = math.atan2(closest_target.y - bot.y, closest_target.x - bot.x)
                bot.is_sprinting = (closest_dist < 150)
    else:
        if random.random() < 0.03: 
            bot.target_angle = random.uniform(0, 2 * math.pi)
            bot.is_sprinting = False

# --- Pełna Detekcja Kolizji i Fizyki Przejęcia Masy ---
def check_coll(s1, s2):
    if not s1.alive or not s2.alive or s1.id == s2.id: return False
    if s1.immunity > 0 or s2.immunity > 0: return False
    
    # Odległość między głowami węży
    dist = math.hypot(s1.x - s2.x, s1.y - s2.y)
    if dist < (s1.radius + s2.radius):
        diff = s1.length - s2.length
        # System dominacji zależy od drastycznej różnicy wielkości segmentów
        if abs(diff) > 40:
            if diff > 0: 
                s1.alive = False
                s2.immunity = 90
                s2.grow(20)
            else: 
                s2.alive = False
                s1.immunity = 90
                s1.grow(20)
        else: 
            # Równe siły skutkują zniszczeniem obu główek
            s1.alive = False
            s2.alive = False
        return True
        
    # Kolizja głowy s1 z segmentami ciała s2 (klasyczna eliminacja)
    for seg in s2.segments[3:]:
        sx, sy = seg
        if math.hypot(s1.x - sx, s1.y - sy) < (s1.radius + 8):
            s1.alive = False
            s2.grow(s1.length // 3)
            return True
            
    return False

def check_food_and_hazards(s):
    # Sprawdzanie zbierania jedzenia
    for f in foods[:]:
        if math.hypot(s.x - f["x"], s.y - f["y"]) < (s.radius + 7):
            if f["is_poison"]:
                # Zatrute jedzenie skraca ogon i osłabia węża!
                s.segments = s.segments[:max(10, s.length - 4)]
                s.points = max(0, s.points - 30)
            else:
                s.grow(f["value"])
                
            if f in foods: foods.remove(f)
            return True
            
    # Sprawdzanie zbierania jaj (inkubacja potomka)
    for egg in eggs[:]:
        if math.hypot(s.x - egg["x"], s.y - egg["y"]) < (s.radius + 12):
            if egg["team_id"] == s.team_id:
                # Wyklucie dziecka (nowy bot w zespole)
                child = Snake(egg["x"], egg["y"], s.color, f"Child of {s.name}", s.team_id, is_player=False, is_offspring=True, skin=s.skin)
                snakes[child.id] = child
                if egg in eggs: eggs.remove(egg)
    return False

def handle_bestie_succession(player, bots):
    current_bestie = next((b for b in bots if b.is_friend and b.alive), None)
    if not current_bestie:
        candidates = [b for b in bots if b.is_offspring and b.alive and b.team_id == player.team_id]
        if candidates:
            new_bestie = max(candidates, key=lambda x: len(x.segments))
            new_bestie.is_offspring = False
            new_bestie.is_friend = True
            new_bestie.is_legacy = True
            new_bestie.grief_boost_timer = 600
            new_bestie.name = f"Bestie (of {player.name})"
            return True
    return False

# --- Asynchroniczny Lifespan Zarządcy Serwera ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    asyncio.create_task(game_loop())
    yield

app = FastAPI(lifespan=lifespan)

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    clients.append(websocket)
    player_id = None
    try:
        while True:
            data = await websocket.receive_text()
            data_json = json.loads(data)
            
            if data_json["type"] == "join":
                team_id = random.randint(0, 5)
                x_pos = random.randint(600, WORLD_WIDTH - 600)
                y_pos = random.randint(600, WORLD_HEIGHT - 600)
                skin = data_json.get("skin", "classic")
                if skin not in SKINS: skin = "classic"
                
                player = Snake(x_pos, y_pos, TEAMS[team_id], data_json["name"], team_id, is_player=True, skin=skin)
                player_id = player.id
                snakes[player_id] = player
                
                bestie = Snake(x_pos + 60, y_pos + 60, TEAMS[team_id], "Bestie", team_id, is_friend=True, skin=skin)
                snakes[bestie.id] = bestie
                
                await websocket.send_json({"type": "joined", "player_id": player_id})
            
            elif data_json["type"] == "move":
                p_id = data_json.get("player_id")
                if p_id in snakes:
                    s = snakes[p_id]
                    s.target_angle = math.atan2(data_json["targetY"] - s.y, data_json["targetX"] - s.x)
                    s.is_sprinting = data_json.get("sprint", False)
                    
            elif data_json["type"] == "action":
                # Nowa unikalna akcja: Zrzucenie trującego śladu lub złożenie jaja
                p_id = data_json.get("player_id")
                if p_id in snakes:
                    s = snakes[p_id]
                    action_type = data_json.get("action")
                    
                    if action_type == "poison" and s.poison_cooldown == 0 and s.length > 25:
                        s.poison_cooldown = 150 # Cooldown na truciznę
                        s.segments.pop()
                        # Pozostawienie fioletowej, trującej kropki na mapie
                        foods.append({"x": s.x, "y": s.y, "value": 0, "is_poison": True})
                        
                    elif action_type == "egg" and s.energy_to_lay_egg >= 45 and s.length > 35:
                        s.energy_to_lay_egg = 0
                        # Skrócenie ogona o koszt wygenerowania nowego jaja
                        s.segments = s.segments[:max(15, s.length - 8)]
                        eggs.append({"x": s.x, "y": s.y, "team_id": s.team_id})
                    
    except WebSocketDisconnect:
        if websocket in clients: clients.remove(websocket)
        if player_id is not None and player_id in snakes: del snakes[player_id]

# --- Główny Wątek Fizyki Świata Gry ---
async def game_loop():
    gen_food(MAX_FOOD)
    for _ in range(MAX_BOTS): 
        b = spawn_bot()
        snakes[b.id] = b
        
    while True:
        # 1. Aktualizacja Sztucznej Inteligencji i Ruchu
        for s in list(snakes.values()):
            if s.alive:
                if not s.is_player:
                    update_bot_ai(s)
                    # Automatyczne składanie jaj przez duże boty AI
                    if s.energy_to_lay_egg >= 60 and s.length > 40 and len(eggs) < 15:
                        s.energy_to_lay_egg = 0
                        s.segments = s.segments[:max(15, s.length - 8)]
                        eggs.append({"x": s.x, "y": s.y, "team_id": s.team_id})
                
                s.move()
                check_food_and_hazards(s)
                handle_bestie_succession(s, list(snakes.values()))
                
        # Rainbow efekt tęczy
        for s in list(snakes.values()):
            if s.skin == "rainbow": s.hue = (s.hue + 3) % 360
                
        # 2. Obliczanie Kolizji (Każdy z każdym)
        snake_list = list(snakes.values())
        for i, a in enumerate(snake_list):
            for b in snake_list[i+1:]:
                if a.alive and b.alive:
                    check_coll(a, b)
                    check_coll(b, a)
                    
        # 3. Czyszczenie Truposzy i Regeneracja Ekosystemu
        for s in list(snakes.values()):
            if not s.alive:
                for seg in s.segments: 
                    seg_x, seg_y = seg # Bezpieczne rozpakowanie bez nawiasów kwadratowych
                    foods.append({"x": seg_x, "y": seg_y, "value": random.randint(1, 2), "is_poison": False})
                if s.id in snakes: del snakes[s.id]
                    
        # Uzupełnianie stanów minimalnych flory mapy
        while len(foods) < MAX_FOOD: 
            foods.append({
                "x": random.randint(30, WORLD_WIDTH - 30), 
                "y": random.randint(30, WORLD_HEIGHT - 30), 
                "value": random.randint(1, 3),
                "is_poison": False
            })
            
        if len([b for b in snakes.values() if not b.is_player]) < MAX_BOTS:
            b = spawn_bot()
            snakes[b.id] = b
            
        # 4. Wyliczanie Oficjalnej Tabeli Wyników (Leaderboard) - Top 7
        sorted_snakes = sorted(snakes.values(), key=lambda x: x.points, reverse=True)
        leaderboard_data = [{"name": s.name, "score": s.points, "isPlayer": s.is_player} for s in sorted_snakes[:7]]
            
        # Budowanie pełnego stanu sieciowego
        state = {
            "type": "update", 
            "snakes": [s.to_dict() for s in snakes.values() if s.alive], 
            "foods": foods[:120],
            "eggs": eggs,
            "leaderboard": leaderboard_data
        }
        
        for c in clients:
            try: await c.send_json(state)
            except: pass
        await asyncio.sleep(1/30)

# Statyczny montaż frontendu pod główny URL usługi
app.mount("/", StaticFiles(directory=".", html=True), name="static")

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    print(f"🐍 SliderSnake Server started on port {port}...")
    uvicorn.run(app, host="0.0.0.0", port=port)
