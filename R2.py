import zmq
import json
import random
import time
from enum import Enum
import math

class Performative(Enum):
    ANNOUNCE_TASK = "announce_task"
    BID = "bid"
    AWARD_TASK = "award_task"
    NEGOTIATE = "negotiate"
    COMPLETE_TASK = "complete_task"
    STARTUP = "startup"
    MOVEMENT = "movement"

class RobotAgent:
    def __init__(self, robot_id="R2"):  # R2
        self.robot_id = robot_id
        self.context = zmq.Context()
        self.socket = self.context.socket(zmq.REQ)
        self.socket.connect("tcp://localhost:5555")
        self.balance = 10
        self.position = (9, 0)
        self.movement_cost = 0.1

    def calculate_bid(self, task):
        # R2 has a slightly different bidding strategy than R1
        task_pos = task["position"]
        distance = abs(self.position[0] - task_pos[0]) + abs(self.position[1] - task_pos[1])
        movement_cost = distance * self.movement_cost
        
        # R2 bids more aggressively than R1
        total_cost = movement_cost + 0.3  # Lower markup than R1
        if total_cost > self.balance:
            return float('inf')
        return total_cost

    def move_one_step(self, current_pos, target_pos):
        x, y = current_pos
        tx, ty = target_pos
        
        # R2 prefers vertical movement first (different from R1)
        if y < ty:
            return (x, y + 1)
        elif y > ty:
            return (x, y - 1)
        elif x < tx:
            return (x + 1, y)
        elif x > tx:
            return (x - 1, y)
        return current_pos

    def move_to_position(self, target_pos):
        print(f"\n{self.robot_id} moving from {self.position} to {target_pos}")
        
        while self.position != target_pos:
            # Calculate next step
            next_pos = self.move_one_step(self.position, target_pos)
            
            # Update position and balance
            self.position = next_pos
            self.balance -= self.movement_cost
            
            # Report movement to server
            message = {
                "sender": self.robot_id,
                "performative": "movement",
                "content": {
                    "position": self.position
                }
            }
            self.socket.send_string(json.dumps(message))
            response = json.loads(self.socket.recv_string())
            
            print(f"{self.robot_id} moved to {self.position}. Balance: {self.balance:.1f}")
            time.sleep(2)  # 2-second delay between movements

    def submit_bid(self, task):
        bid_amount = self.calculate_bid(task)
        message = {
            "sender": self.robot_id,
            "performative": Performative.BID.value,
            "content": {
                "task_id": task["task_id"],
                "bid_amount": bid_amount
            }
        }
        
        print(f"\n{self.robot_id} bidding {bid_amount:.1f} for task {task['task_id']}")
        self.socket.send_string(json.dumps(message))
        response = json.loads(self.socket.recv_string())
        return response

    def execute_task(self, task):
        if not task:
            return None
            
        # Move to task location
        target_pos = tuple(task["position"])
        self.move_to_position(target_pos)
        
        # Execute task
        print(f"\n{self.robot_id} executing task {task['task_id']}: {task['description']}")
        time.sleep(10)  # 10-second task execution
        
        # Report task completion
        message = {
            "sender": self.robot_id,
            "performative": Performative.COMPLETE_TASK.value,
            "content": {
                "task_id": task["task_id"],
                "position": self.position
            }
        }
        self.socket.send_string(json.dumps(message))
        response = json.loads(self.socket.recv_string())
        
        # Update balance with reward
        if "reward" in response["content"]:
            self.balance += response["content"]["reward"]
            print(f"{self.robot_id} completed task. New balance: {self.balance:.1f}")
        
        return response

    def run(self):
        print(f"\n{self.robot_id} running...")
        
        # Startup synchronization
        startup_message = {
            "sender": self.robot_id,
            "performative": "startup",
            "content": None
        }
        print(f"\n{self.robot_id} waiting for other robot to connect...")
        
        current_task = None
        
        while True:
            self.socket.send_string(json.dumps(startup_message))
            response = json.loads(self.socket.recv_string())
            
            if response["performative"] == "start_bidding":
                print(f"\n{self.robot_id} starting bidding process")
                first_task = response["content"]["first_task"]
                if first_task:
                    current_task = first_task
                    response = self.submit_bid(current_task)
                break
            elif response["performative"] == "wait":
                print(f"\n{self.robot_id} waiting for other robot...")
                time.sleep(1)
                continue
        
        # Main loop
        while True:
            try:
                if response["performative"] == "no_more_tasks":
                    print(f"\n{self.robot_id} finished all tasks. Final balance: {self.balance:.1f}")
                    print(f"Server reported final balance: {response['content']['final_balance']:.1f}")
                    break
                
                if response["performative"] == "error":
                    print(f"\nError from server: {response['content']['message']}")
                    time.sleep(1)
                    
                    # Resubmit current state to server
                    if current_task:
                        response = self.submit_bid(current_task)
                    else:
                        # If no current task, send startup message again
                        self.socket.send_string(json.dumps(startup_message))
                        response = json.loads(self.socket.recv_string())
                    continue
                
                if response["performative"] == "wrong_task":
                    current_task = response["content"]["correct_task"]
                    response = self.submit_bid(current_task)
                    continue
                
                if response["performative"] == "waiting_for_bids":
                    if "current_task" in response["content"]:
                        current_task = response["content"]["current_task"]
                    time.sleep(0.5)
                    response = self.submit_bid(current_task)
                    continue
                
                if response["performative"] == Performative.AWARD_TASK.value:
                    if response["content"]["winner"] == self.robot_id:
                        # We won the task
                        task_to_execute = current_task  # Execute current task we just won
                        task_result = self.execute_task(task_to_execute)
                        
                        # Check for next task from response
                        next_task = response["content"]["next_task"]
                        
                        # Check if there are no more tasks
                        if next_task is None:
                            # Send a final bid to get the no_more_tasks response
                            dummy_message = {
                                "sender": self.robot_id,
                                "performative": Performative.BID.value,
                                "content": {
                                    "task_id": -1,  # Invalid task ID to trigger end
                                    "bid_amount": 0
                                }
                            }
                            self.socket.send_string(json.dumps(dummy_message))
                            response = json.loads(self.socket.recv_string())
                            continue
                        
                        # Otherwise bid on the next task
                        current_task = next_task
                        response = self.submit_bid(current_task)
                    else:
                        # We didn't win, bid on next task
                        next_task = response["content"]["next_task"]
                        if next_task is None:
                            # Send a final bid to get the no_more_tasks response
                            dummy_message = {
                                "sender": self.robot_id,
                                "performative": Performative.BID.value,
                                "content": {
                                    "task_id": -1,  # Invalid task ID to trigger end
                                    "bid_amount": 0
                                }
                            }
                            self.socket.send_string(json.dumps(dummy_message))
                            response = json.loads(self.socket.recv_string())
                        else:
                            current_task = next_task
                            response = self.submit_bid(current_task)
                
                # Handle task_completed message
                if response["performative"] == "task_completed":
                    next_task = response["content"]["next_task"]
                    if next_task is None:
                        # Send a final bid to get the no_more_tasks response
                        dummy_message = {
                            "sender": self.robot_id,
                            "performative": Performative.BID.value,
                            "content": {
                                "task_id": -1,  # Invalid task ID to trigger end
                                "bid_amount": 0
                            }
                        }
                        self.socket.send_string(json.dumps(dummy_message))
                        response = json.loads(self.socket.recv_string())
                    else:
                        current_task = next_task
                        response = self.submit_bid(current_task)
                
                time.sleep(0.1)
                
            except Exception as e:
                print(f"\nError: {e}")
                time.sleep(1)

if __name__ == "__main__":
    robot = RobotAgent()
    robot.run()