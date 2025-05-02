# multirobot
System Overview
The system consists of three main components:

A central server that manages tasks and handles the auction process
Robot R1 (starting at position 0,0)
Robot R2 (starting at position 9,0)

Each robot has its own bidding strategy, movement pattern, and task execution capabilities. The central server uses a contract net protocol style of task allocation with bidding.
Key Observations
Based on the code and execution logs:

Bidding Strategies:

R1 bids with a markup of 0.5 on top of movement costs
R2 bids more aggressively with a markup of only 0.3
This difference gives R2 a significant advantage in winning tasks


Movement Patterns:

R1 prefers horizontal movement first
R2 prefers vertical movement first
Movement costs 0.1 per grid cell for both robots


Execution Results:

R2 won all 5 tasks
R1's balance remained at 10.0 (initial value)
R2's final balance increased to 28.1
Each completed task provides a reward of 5 units

 Components:

Central Agent (Server):
Manages a 10x10 grid environment visualized with Pygame
Handles task allocation through an auction mechanism
Tracks robot positions, balances, and task completion
Uses ZMQ REP socket for communication
Robot Agents (R1 and R2):
Start at opposite corners (R1 at 0,0, R2 at 9,0)
Have different movement strategies (R1 prefers horizontal, R2 prefers vertical)
Use different bidding strategies (R2 bids more aggressively with lower markup)
Use ZMQ REQ sockets to communicate with server
Task System:
5 randomly positioned tasks on the grid
Tasks have IDs, descriptions, costs, and positions
Tasks are allocated through bidding
Completion rewards 5 currency units
Communication Protocol:

Uses JSON messages with performatives like ANNOUNCE_TASK, BID, AWARD_TASK
Handles startup synchronization, bidding, movement, and task completion
Includes error handling and task verification
Main Workflow:

Robots connect and synchronize
Server announces tasks one by one
Robots bid based on distance and strategy
Winner moves to task location and executes
Process repeats until all tasks complete
