import asyncio
import json
import math
import random
import uuid
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse

app = FastAPI()

# Zezwolenie na CORS (niezbędne, jeśli frontend i backend są pod różnymi adresami)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- STAN GRY (Zunifikowana Wersja) ---
GAME_WIDTH = 5000
GAME_HEIGHT = 5000
snakes = {}
foods = []
eggs = []
clients = set()

# Generowanie startowego jedzenia na arenie
def spawn_food(is_poison=False):
    return {
        "x": random.randint(100, GAME_WIDTH - 100),
        "y": random.randint(100, GAME_HEIGHT - 100),
        "value": random.randint(1, 5),
        "is_poison": is_poison
    }

for _ in range(400):
    foods.append(spawn_food(is_poison=False))
for _ in range(50):
    foods.append(spawn_food(is_poison=True))


# --- ENDPOINTY ---

@app.get("/")
async def root():
    # To jest kluczowe dla funkcji wakeUpServer() na frontendzie.
    # Wysłanie zapytania GET wybudza instancję na Renderze.
    return {"status": "SliderSnake Server is Awake and Ready!"}

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    clients.add(websocket)
    player_id = str(uuid.uuid4())

    try:
        while True:
            data = await websocket.receive_text()
            msg = json.loads(data)
            
            if msg["type"] == "join":
                name = msg.get("name", f"Player_{random.randint(100,999)}")
                
                # Inicjalizacja nowego węża
                snakes[player_id] = {
                    "id": player_id,
                    "name": name,
                    "skin": msg.get("skin", "classic"),
                    "color": "#4fe171",
                    "x": random.randint(500, GAME_WIDTH - 500),
                    "y": random.randint(500, GAME_HEIGHT - 500),
                    "targetX": 0,
                    "targetY": 0,
                    "sprint": False,
                    "segments": [],
                    "points": 120, # Startowe punkty
                    "level": 1,
                    "isFriend": "Bestie" in name # Znacznik do mechaniki sukcesji
                }
                
                # Ustawienie celu początkowego na miejsce spawnu (żeby wąż nie uciekał)
                snakes[player_id]["targetX"] = snakes[player_id]["x"]
                snakes[player_id]["targetY"] = snakes[player_id]["y"]
                
                for _ in range(5):
                    snakes[player_id]["segments"].append([snakes[player_id]["x"], snakes[player_id]["y"]])

                await websocket.send_json({"type": "joined", "player_id": player_id})
                print(f"➕ Gracz {name} dołączył do gry.")
                
            elif msg["type"] == "move":
                if player_id in snakes:
                    snakes[player_id]["targetX"] = msg["targetX"]
                    snakes[player_id]["targetY"] = msg["targetY"]
                    snakes[player_id]["sprint"] = msg.get("sprint", False)
                    
            elif msg["type"] == "action":
                if player_id in snakes:
                    snake = snakes[player_id]
                    # Mechanika znoszenia jaj (W)
                    if msg["action"] == "egg" and snake["points"] >= 50:
                        snake["points"] -= 30
                        eggs.append({"x": snake["x"], "y": snake["y"]})
                    # Mechanika zostawiania trucizny (Q)
                    elif msg["action"] == "poison" and snake["points"] >= 20:
                        snake["points"] -= 10
                        foods.append({"x": snake["x"], "y": snake["y"], "value": 3, "is_poison": True})
                        
    except WebSocketDisconnect:
        clients.remove(websocket)
        if player_id in snakes:
            print(f"➖ Gracz {snakes[player_id]['name']} rozłączył się.")
            # Zmiana ciała w jedzenie po śmierci/wyjściu
            for segment in snakes[player_id]["segments"][::2]: 
                foods.append({"x": segment[0], "y": segment[1], "value": 5, "is_poison": False})
            del snakes[player_id]

# --- PĘTLA GRY (Game Loop) ---
async def game_loop():
    while True:
        # 1. Obliczanie fizyki dla każdego węża
        for sid, snake in snakes.items():
            dx = snake["targetX"] - snake["x"]
            dy = snake["targetY"] - snake["y"]
            dist = math.hypot(dx, dy)
            
            speed = 8 if snake["sprint"] and snake["points"] > 50 else 4.5
            
            # Jeśli sprintuje, tracimy punkty i zostawiamy małe jedzenie za sobą
            if snake["sprint"] and dist > 0 and snake["points"] > 50:
                snake["points"] -= 0.5
                if random.random() < 0.1:
                    foods.append({"x": snake["segments"][-1][0], "y": snake["segments"][-1][1], "value": 1, "is_poison": False})
            
            if dist > speed:
                snake["x"] += (dx / dist) * speed
                snake["y"] += (dy / dist) * speed
            else:
                snake["x"] = snake["targetX"]
                snake["y"] = snake["targetY"]
                
            # Ograniczenia mapy
            snake["x"] = max(20, min(GAME_WIDTH - 20, snake["x"]))
            snake["y"] = max(20, min(GAME_HEIGHT - 20, snake["y"]))
                
            # Aktualizacja historii segmentów (Ogon podąża za głową)
            snake["segments"].insert(0, [snake["x"], snake["y"]])
            target_length = 5 + (snake["points"] // 40)
            
            while len(snake["segments"]) > target_length:
                snake["segments"].pop()
                
            # 2. Zbieranie jedzenia (Kolizje)
            for f in foods[:]:
                if math.hypot(snake["x"] - f["x"], snake["y"] - f["y"]) < 25:
                    if f["is_poison"]:
                        snake["points"] = max(10, snake["points"] - 50) # Bolesna kara
                    else:
                        snake["points"] += f["value"] * 5
                    foods.remove(f)
                    
                    # Natychmiastowy respawn zjedzonego jedzenia gdzie indziej
                    foods.append(spawn_food(is_poison=random.random() < 0.1))
            
            # Przeliczanie levelu na podstawie punktów
            snake["level"] = max(1, snake["points"] // 100)

        # 3. Aktualizacja Tabeli Liderów
        leaderboard = [{"name": s["name"], "score": int(s["points"])} for s in snakes.values()]
        leaderboard.sort(key=lambda x: x["score"], reverse=True)
        top_10 = leaderboard[:10]

        # 4. Konstrukcja pakietu danych i wysyłka do klientów (Broadcast)
        update_msg = {
            "type": "update",
            "snakes": list(snakes.values()),
            "foods": foods,
            "eggs": eggs,
            "leaderboard": top_10
        }
        
        # Szybka konwersja do JSON przed pętlą ogranicza obciążenie procesora
        json_msg = json.dumps(update_msg)
        
        dead_clients = set()
        for client in clients:
            try:
                await client.send_text(json_msg)
            except Exception:
                dead_clients.add(client)
                
        for dead in dead_clients:
            clients.remove(dead)
                
        # Serwer cyka z prędkością ok. 25 klatek na sekundę
        await asyncio.sleep(0.04) 

@app.on_event("startup")
async def startup_event():
    # Uruchamiamy silnik gry w tle zaraz po starcie serwera
    asyncio.create_task(game_loop())
    print("🚀 SliderSnake Game Engine Started!")
