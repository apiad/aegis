- **`turn_end` refuses done while something will still wake the agent.** A live monitor, a callback queue task, a background task or an approved workflow run starts another turn when it ends, so a done card with its Archive button was wrong. The refusal names what is live and tells the agent to cancel it, end the turn without `turn_end` to wait, or pass review.
help: sessions
