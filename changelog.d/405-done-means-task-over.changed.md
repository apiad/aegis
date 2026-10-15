- **`turn_end` done now means the whole task is over.** Agents passed done on
  turns that only finished a step, and a done card offers an Archive button,
  so the person had to read the message to learn the work went on. The primer
  and the tool schema now say a step passes review, and `turn_end` refuses
  done while the plan has items not done.
help: sessions#what-the-agent-reports
