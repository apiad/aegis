- **A turn that ends without turn_end is asked about within seconds.** The
  idle nudge comes two seconds after such a turn instead of ten minutes, so a
  card does not read done for ten minutes when it was not. `nudges:` in
  `.aegis.yaml` sets the wait and the plan nudge, or turns the idle one off.

help: sessions#what-the-agent-reports
