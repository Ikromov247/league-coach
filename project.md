# League coach

Realtime league of legends coach that gives macro advice based on game state. It takes a screenshot of the game every 3-5 seconds, and extracts:
- Game time
- CS
- KDA
- Teamcomp
- Items
- Positioning
- Map state
- Role 
etc

And gives advice for next action if necessary. The advice should be voiced out via text-to-speech. Advice should only be given when necessary. 

At the end of the game, the coach should give an overview of the game with list of tips to improve performance. 

My vision for this project is to emulate a league coach whose goal is to improve my game skills over time. 

Given current model selection, each full game analysis costs about $1.8 (25 minutes match)