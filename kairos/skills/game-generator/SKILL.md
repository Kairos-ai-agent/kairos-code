---
name: "game-generator"
description: "No-code interactive game generator expert. Create playable HTML5 games instantly from simple descriptions. Use when users need casual games, puzzle games, adventure games, or text-based games. Trigger"
priority: 0.5
imported-from: "minimax"
source-path: "C:\\Users\\you\\.minimax\\skills\\game-generator\\SKILL.md"
---
# Zero-Code Interactive Game Generator

## Core Capabilities

Generate playable HTML5 games from simple descriptions without any coding knowledge.

## Key Features

- **Game Generation**: Complete playable HTML5 games
- **Multiple Genres**: Match-3, Puzzle, Adventure, Text Game, Runner, Tower Defense
- **Level Design**: Progressive difficulty, 10+ levels
- **Character & Props**: Customizable protagonists, items, obstacles
- **Feedback System**: Sound effects, score display, visual feedback
- **Share & Export**: Playable link, download ZIP, share to social

## Workflow

### 1. Receive Requirements

**Required Information**:
- Game Type: Match-3 / Puzzle / Adventure / Text Game / Runner / Tower Defense
- Core Setting: Protagonist, goal, obstacles, items

**Game Types**:

| Type | Core Mechanic | Example |
|------|--------------|---------|
| Match-3 | Match 3+ items to clear | Candy Crush style |
| Puzzle | Find clues, solve puzzles | Escape room style |
| Adventure | Explore, collect, progress | Platformer style |
| Text Game | Choices, story branches | Interactive fiction |
| Runner | Dodge obstacles, survive | Temple Run style |
| Tower Defense | Place towers, stop enemies | Plants vs Zombies style |

**Core Settings to Clarify**:
- Protagonist: Animal, human, character type
- Goal: Collect keys, reach exit, survive
- Obstacles: Enemies, time limit, barriers
- Items: Power-ups, collectibles, tools

**Ask clarifying questions when needed**:
- "Do you want match-3 or puzzle style?"
- "Should the protagonist have special skills?"
- "How many levels do you need?"

### 2. Output Structure

```
# [Game Name] - Generated!

## Play Now
[Live Game URL]

## Game Overview
- **Type**: Match-3 / Puzzle / Adventure / etc.
- **Protagonist**: Cat / Dog / Robot
- **Goal**: Find keys, escape, collect items
- **Levels**: 10 levels with increasing difficulty

## Core Mechanics

### Match-3 Rules
- Click 3+ adjacent matching items to clear
- Score points for each match
- Special items: Row clear, Column clear, Bomb

### Level Design
- Level 1: Easy (5 item types, no obstacles)
- Level 5: Medium (special items appear)
- Level 10: Hard (moving obstacles, time limit)

## Characters & Props
- **Protagonist**: Q-version cat with idle animation
- **Items**: Cake, cookie, tea, milk tea
- **Power-ups**: Cat paw (3x3 clear), Double points

## Modification Guide

### Change Protagonist
Find in code:
```javascript
const player = {
  sprite: 'cat.png', // Replace with 'dog.png'
  speed: 5
};
```

### Add Power-up
```javascript
const powerUps = [
  { name: 'Bomb', effect: 'clear3x3', icon: 'bomb.png' },
  { name: 'Double', effect: 'score*2', icon: 'star.png' }
];
```

### Adjust Difficulty
```javascript
const levelConfig = {
  speed: 1,        // Increase for harder
  itemTypes: 5,     // More types = harder
  obstacles: 0      // Add obstacles
};
```

### Change Background
```javascript
const gameConfig = {
  background: 'tea-shop.jpg', // Replace with 'space.jpg'
  music: 'calm.mp3'
};
```

## Game Structure

### File Structure
```
game/
├── index.html      # Main game page
├── game.js         # Game logic
├── style.css       # Game styles
└── assets/         # Images, sounds
```

### Code Organization
```javascript
// Player movement logic
// Collision detection
// Scoring system
// Level progression
// Power-up effects
// Sound effects
```

## Deployment Options

### Option 1: Direct Link
- Generate shareable URL
- Playable immediately
- Mobile and PC compatible

### Option 2: Download ZIP
- Complete game package
- Self-hosted
- Custom domain ready

### Option 3: Embed
- iframe code for websites
- WordPress plugin
- Blog embed

## Game Quality Standards

### Must Have
- Clear goal (win/lose condition)
- Feedback (score, sounds, animations)
- Challenge (difficulty progression)
- Replayability (multiple levels)

### Should Have
- Tutorial/instructions
- Pause menu
- Progress save
- Leaderboard

### Nice to Have
- Achievements
- Unlockables
- Multiple endings
- Multiplayer mode

## Style Guidelines

### Pixel Art Style
- 8-bit graphics
- Retro sound effects
- Limited color palette
- Chunky pixels

### Cartoon Style
- Bright colors
- Q-version characters
- Smooth animations
- Cute expressions

### Minimalist Style
- Simple shapes
- Clean lines
- Limited palette
- Focus on gameplay

### Retro Style
- 16-bit era graphics
- Nostalgic colors
- Classic soundtracks
- Familiar mechanics

## Common Game Templates

### Match-3 Template
```
- Grid of items (8x8)
- Click to swap adjacent
- Match 3+ to clear
- Special matches = power-ups
- Level goals: score/clear items
```

### Puzzle Template
```
- Scene with hidden objects
- Click to collect clues
- Combine items to solve
- Escape when puzzle solved
```

### Runner Template
```
- Auto-scrolling scene
- Jump/duck to dodge
- Collect coins/boosts
- Distance = score
```

### Tower Defense Template
```
- Place towers on map
- Enemies follow path
- Kill enemies to earn
- Upgrade towers
```
