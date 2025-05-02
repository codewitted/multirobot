import zmq
import json
import random
import time
import pygame
from enum import Enum
import math

class Performative(Enum):
    ANNOUNCE_TASK = "announce_task"
    BID = "bid"
    AWARD_TASK = "award_task"
    NEGOTIATE = "negotiate"
    COMPLETE_TASK = "complete_task"
    TRANSFER_CURRENCY = "transfer_currency"
    STARTUP = "startup"
    MOVEMENT = "movement"

class Task:
    def __init__(self, task_id, description, base_cost, position):
        self.task_id = task_id
        self.description = description
        self.base_cost = base_cost
        self.assigned_to = None
        self.status = "pending"
        self.position = position
    
    def to_dict(self):
        return {
            "task_id": self.task_id,
            "description": self.description,
            "base_cost": self.base_cost,
            "status": self.status,
            "position": self.position
        }

class CentralAgent:
    def __init__(self):
        self.context = zmq.Context()
        self.socket = self.context.socket(zmq.REP)
        self.socket.bind("tcp://*:5555")
        
        pygame.init()
        self.cell_size = 100
        self.grid_size = 10
        self.window_size = self.cell_size * self.grid_size
        self.screen = pygame.display.set_mode((self.window_size, self.window_size))
        pygame.display.set_caption("Task Allocation Grid")
        
        self.WHITE = (255, 255, 255)
        self.BLACK = (0, 0, 0)
        self.RED = (255, 0, 0)
        self.BLUE = (0, 0, 255)
        self.GREEN = (0, 255, 0)
        
        positions = self.generate_random_positions(5)
        
        self.tasks = [
            Task(1, "Pick", 1, positions[0]),
            Task(2, "Nav", 1, positions[1]),
            Task(3, "Inspect", 1, positions[2]),
            Task(4, "Assembly", 1, positions[3]),
            Task(5, "Check", 1, positions[4])
        ]
        
        self.completed_tasks = []
        self.robot_positions = {"R1": (0, 0), "R2": (9, 0)}
        self.robot_balances = {"R1": 10, "R2": 10}
        self.current_task_index = 0
        self.bids = {}
        self.robots_ready = set()
        self.all_tasks_completed = False

    def generate_random_positions(self, num_positions):
        positions = []
        while len(positions) < num_positions:
            pos = (random.randint(0, 9), random.randint(0, 9))
            if pos != (0, 0) and pos not in positions:
                positions.append(pos)
        return positions

    def draw_grid(self):
        self.screen.fill(self.WHITE)
        
        # Draw grid lines
        for i in range(self.grid_size + 1):
            pygame.draw.line(self.screen, self.BLACK, 
                           (i * self.cell_size, 0), 
                           (i * self.cell_size, self.window_size))
            pygame.draw.line(self.screen, self.BLACK, 
                           (0, i * self.cell_size), 
                           (self.window_size, i * self.cell_size))
        
        # Draw tasks
        font = pygame.font.Font(None, 36)
        for task in self.tasks:
            if task not in self.completed_tasks:
                x, y = task.position
                text = font.render(f"T{task.task_id}", True, self.BLACK)
                text_rect = text.get_rect(center=(x * self.cell_size + self.cell_size/2,
                                                y * self.cell_size + self.cell_size/2))
                self.screen.blit(text, text_rect)
        
        # Draw completed tasks count
        completed_text = f"Completed: {len(self.completed_tasks)}/{len(self.tasks)}"
        text = font.render(completed_text, True, self.GREEN)
        text_rect = text.get_rect(midtop=(self.window_size/2, 10))
        self.screen.blit(text, text_rect)
        
        # Draw robots and their balances
        for robot_id, pos in self.robot_positions.items():
            x, y = pos
            color = self.RED if robot_id == "R1" else self.BLUE
            pygame.draw.circle(self.screen, color,
                             (x * self.cell_size + self.cell_size/2,
                              y * self.cell_size + self.cell_size/2),
                             self.cell_size/4)
            
            # Draw robot labels and balances
            balance_text = f"{robot_id}: {self.robot_balances[robot_id]:.1f}"
            text = font.render(balance_text, True, color)
            if robot_id == "R1":
                text_rect = text.get_rect(topleft=(10, 10))
            else:
                text_rect = text.get_rect(topright=(self.window_size - 10, 10))
            self.screen.blit(text, text_rect)
        
        pygame.display.flip()

    def handle_startup(self, message):
        robot_id = message["sender"]
        self.robots_ready.add(robot_id)
        print(f"\n{robot_id} connected. Waiting for {2 - len(self.robots_ready)} more robots...")
        
        if len(self.robots_ready) == 2:
            print("\nAll robots connected. Starting task allocation...")
            return {
                "performative": "start_bidding",
                "content": {
                    "message": "All robots connected, start bidding",
                    "first_task": self.announce_task()
                }
            }
        return {
            "performative": "wait",
            "content": {
                "message": "Waiting for other robot"
            }
        }

    def update_robot_position(self, robot_id, new_position):
        self.robot_positions[robot_id] = new_position
        self.draw_grid()

    def announce_task(self):
        if self.current_task_index >= len(self.tasks):
            self.all_tasks_completed = True
            return None
        return self.tasks[self.current_task_index].to_dict()

    def handle_bid(self, message):
        robot_id = message["sender"]
        bid_amount = message["content"]["bid_amount"]
        task_id = message["content"]["task_id"]
        
        # Check if we're out of tasks
        if self.all_tasks_completed or self.current_task_index >= len(self.tasks):
            self.all_tasks_completed = True
            return {
                "performative": "no_more_tasks",
                "content": {
                    "message": "All tasks completed",
                    "final_balance": self.robot_balances[robot_id]
                }
            }
        
        # Handle invalid task_id (end of tasks indicator)
        if task_id == -1:
            return {
                "performative": "no_more_tasks",
                "content": {
                    "message": "All tasks completed",
                    "final_balance": self.robot_balances[robot_id]
                }
            }
        
        current_task = self.tasks[self.current_task_index]
        
        # If robot is bidding for wrong task, send current task info
        if task_id != current_task.task_id:
            return {
                "performative": "wrong_task",
                "content": {
                    "correct_task": current_task.to_dict()
                }
            }
        
        print(f"\n{robot_id} bids {bid_amount:.1f} for task {task_id}")
        self.bids[robot_id] = bid_amount
        
        # If we have bids from both robots
        if len(self.bids) == 2:
            winner = self.determine_winner()
            print(f"\nTask {task_id} awarded to {winner}")
            
            # Clear bids and move to next task
            self.bids = {}
            self.current_task_index += 1
            next_task = self.announce_task()
            
            return {
                "performative": Performative.AWARD_TASK.value,
                "content": {
                    "winner": winner,
                    "task_id": task_id,
                    "next_task": next_task
                }
            }
        
        return {
            "performative": "waiting_for_bids",
            "content": {
                "current_task": current_task.to_dict()
            }
        }

    def determine_winner(self):
        if self.bids["R1"] == self.bids["R2"]:
            return random.choice(["R1", "R2"])
        return min(self.bids, key=self.bids.get)

    def run(self):
        print("\nCentral Agent running...")
        self.draw_grid()
        
        while True:
            try:
                for event in pygame.event.get():
                    if event.type == pygame.QUIT:
                        pygame.quit()
                        return
                
                message = json.loads(self.socket.recv_string())
                
                if message["performative"] == "startup":
                    response = self.handle_startup(message)
                elif message["performative"] == Performative.BID.value:
                    response = self.handle_bid(message)
                elif message["performative"] == Performative.COMPLETE_TASK.value:
                    robot_id = message["sender"]
                    task_id = message["content"]["task_id"]
                    
                    if "position" in message["content"]:
                        self.update_robot_position(robot_id, message["content"]["position"])
                    
                    # Add task to completed tasks
                    completed_task_index = task_id - 1
                    if 0 <= completed_task_index < len(self.tasks):
                        completed_task = self.tasks[completed_task_index]
                        if completed_task not in self.completed_tasks:
                            self.completed_tasks.append(completed_task)
                    
                    # Give reward
                    reward = 5
                    self.robot_balances[robot_id] += reward
                    print(f"\n{robot_id} completed task {task_id}. Reward: {reward}")
                    
                    next_task = self.announce_task()
                    response = {
                        "performative": "task_completed",
                        "content": {
                            "reward": reward,
                            "new_balance": self.robot_balances[robot_id],
                            "next_task": next_task
                        }
                    }
                    
                    # Update display after task completion
                    self.draw_grid()
                elif message["performative"] == "movement":
                    self.update_robot_position(message["sender"], message["content"]["position"])
                    response = {"performative": "movement_updated", "content": None}
                else:
                    response = {
                        "performative": "error",
                        "content": {"message": f"Unknown performative: {message['performative']}"}
                    }
                
                self.socket.send_string(json.dumps(response))
                
            except Exception as e:
                print(f"\nError: {e}")
                error_response = {
                    "performative": "error",
                    "content": {"message": str(e)}
                }
                self.socket.send_string(json.dumps(error_response))

if __name__ == "__main__":
    server = CentralAgent()
    server.run()