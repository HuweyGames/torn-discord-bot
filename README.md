# Torn City Faction Bot
A Discord bot for Torn City faction verification. Verifies players' faction membership, updates their nicknames, and assigns roles based on their faction position.

## Features
- **Faction Verification**: Users run `/join` to submit their public Torn API key via DM.
- **Nickname Update**: Sets Discord nickname to `DiscordName (TornPlayerName)`.
- **Role Assignment**: Assigns roles like `Faction Leader`, `Faction Member` based on Torn faction position.
- **Background Checks**: Hourly checks to remove roles if users leave the faction.
- **Secure Storage**: API keys stored in `api_keys.json` (not committed).

## Setup
1. **Clone the Repository**:
   ```bash
   git clone https://github.com/your-username/torn-city-faction-bot.git
   cd torn-city-faction-bot
