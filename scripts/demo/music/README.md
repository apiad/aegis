# The film's music bed

`film.mp3` is the music under the landing film's narration
(`beats/film.py` names it as `MUSIC`; `director.py --music` overrides it).

It was generated on 2026-10-23 with ElevenLabs Music (`POST /v1/music`,
`music_v1`, `force_instrumental: true`, 210 s, `mp3_44100_128`), on a
self-serve paid plan, whose terms allow online commercial use of the output
except in film, TV and games. The prompt:

> Instrumental background music for a calm, confident software product
> explainer video. Warm minimal electronic with soft felt piano, gentle analog
> synth pads, a light steady pulse at 96 BPM, subtle brushed percussion
> entering after 20 seconds. Hopeful and focused, never dramatic, no big
> drops, no vocals. Even energy throughout so a narrator can speak over it,
> with a soft ending.

The director loops it to the film's length, fades both ends and ducks it
under each line, so a film longer than 210 s still works; one much longer
should get a longer track.
