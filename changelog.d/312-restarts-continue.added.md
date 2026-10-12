A restart of `aegis serve` continues the turns it interrupted, and sends the
messages sessions were holding, instead of leaving each one for a person to restart.
After a crash a turn continues once, and a harness the crash left running is ended
first. A turn that restarts cut twice in a row, or a harness that failed on its own,
waits for a person. (#312)
help: sessions#when-the-server-restarts
