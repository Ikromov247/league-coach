# League coach

Realtime league of legends coach that gives macro and laning advice based on game state. It takes a screenshot of the game every 3-5 seconds, and extracts:
- Game time
- CS
- KDA
- Teamcomp
- Items
- Positioning
- Map state
- Role 
etc

And gives advice for next action if necessary. The advice is voiced out via text-to-speech. Advice is only given when necessary. 

At the end of the game, the coach gives an overview of the game with list of tips to improve performance. 

My vision for this project is to emulate a league coach whose goal is to improve my game skills over time. 

Given current model selection, each full game analysis and in-game commentary costs about $1.8 (for a 25 minutes match). If only post-game analysis and pre-game briefing are included (`--brief-only` option), then it costs $0.20.
