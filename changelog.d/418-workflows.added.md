- **Workflows.** An agent can write a piece of work as a workflow: a tree in which every node says what it does. The person approves it from a card in the transcript, the server runs each step as a locked session, and the card tells what happened. It runs steps in sequence or side by side, maps a step over a list, and keeps the server under a pool of agents (`workflows: {max_agents}`), in total and per agent.

help: workflows
