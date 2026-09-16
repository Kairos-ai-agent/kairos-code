---
name: "board-game-rule-translator"
description: "Translate English board game rulebooks, manuals, and game instructions into Chinese. Use when users ask to translate board game rules, game manuals, or rulebooks. Includes OCR capabilities for scanned"
priority: 0.5
imported-from: "minimax"
source-path: "C:\\Users\\you\\.minimax\\skills\\board-game-rule-translator\\SKILL.md"
---
# Board Game Rule Translator

A specialized translation skill for English board game rulebooks with OCR support and expert knowledge in board game terminology.

## Core Capabilities

- **OCR Recognition**: Extract text from scanned PDFs and images
- **Professional Translation**: Accurate translation of board game terminology
- **Format Preservation**: Maintain original document structure and formatting
- **Terminology Consistency**: Use standardized board game terminology glossary

## Supported Input Formats

| Format | Input Method |
|--------|-------------|
| Digital PDF | Direct upload |
| Scanned PDF | OCR processing |
| Images (JPG/PNG) | OCR processing |
| Plain text | Direct paste |
| Website URL | Content extraction |

## Translation Strategy (Critical Rules)

> 用户原始要求（必须遵守）:
> 1. 不要自行优化格式，只需要给我无格式纯文本就好，允许换行
> 2. 确保不会自行增加非原文内容避免增加人工校对工作量
> 3. 避免自行判断不重要的文字导致忽略翻译

### Mandatory Output Format
- **Plain text only**: Provide pure Chinese translation without any formatting (headers, lists, tables)
- **Allow newlines**: Use newlines for paragraph breaks only
- **No added content**: Do NOT add explanations, comments, or supplementary content
- **No format optimization**: Do NOT reformat, restructure, or improve the original layout

### What to Translate
- **Translate EVERYTHING**: All game rules, instructions, descriptions, notes, warnings
- **Do NOT skip**: Even "unimportant" looking text may contain critical rules
- **No judgment calls**: Translate all text regardless of perceived importance

### What NOT to Translate (Keep Original)
- **Proper Nouns**: Person names (Andrew Carnegie, J.P. Morgan, etc.)
- **Company Names**: Publisher names, brand names
- **Website URLs**: Web addresses, links
- **Copyright Notices**: Legal text, version numbers (v2026.01)
- **Meaningless Text**: Placeholder text like "Lorem ipsum", gibberish
- **Product Identifiers**: Component codes, model numbers

### Translation Principles
1. **Accuracy over elegance**: Stay close to original meaning
2. **Consistency**: Use standardized board game terminology
3. **Completeness**: Never skip sections or omit text
4. **Minimal intervention**: Only translate what needs translating

## Translation Workflow

### Step 1: Input Processing

1. **File Upload**
   - Accept PDF, JPG, PNG formats
   - Maximum file size: 50MB

2. **OCR Detection**
   - Automatically detect if image-based content requires OCR
   - Use `extract_pdfs_key_info` for digital PDFs
   - Use `images_understand` for image OCR

3. **Content Extraction**
   - Extract full text content
   - Preserve paragraph structure
   - Identify section headers and numbered lists

### Step 2: Translation Process

1. **Text Analysis**
   - Identify game-specific terminology
   - Detect special formatting (tables, diagrams)
   - Parse numbered rules and bullet points

2. **Terminology Translation**
   - Apply standardized board game terms (see `references/board-game-terminology.md`)
   - Maintain consistent translation for recurring terms
   - Use context-aware translation for ambiguous words

3. **Format Translation**
   - Translate table headers and content
   - Preserve bullet point hierarchy
   - Keep numbered list structure intact

### Step 3: Output Generation

1. **Primary Output**: Translated markdown document
2. **Optional Output**: Formatted HTML for printing
3. **Reference Output**: Terminology mapping table (EN → CN)

## Key Terminology Categories

### Game Components
- Meeple → 棋子/米宝
- Token → 指示物
- Card → 卡牌
- Dice/Die → 骰子
- Tile → 板块/砖块
- Board → 游戏板
- Playmat → 垫子
- Cube → 方块指示物

### Game Mechanics
- Worker Placement → 工人放置
- Deck Building → 牌库构建
- Area Control → 区域控制
- Tile Placement → 板块放置
- Hand Management → 手牌管理
- Engine Building → 引擎构建
- Press Your Luck → 赌运气
- Hidden Role → 隐藏身份
- Trick-Taking → 吃墩
- Card Drafting → 卡牌轮抽

### Game States
- Turn → 回合
- Round → 轮
- Phase → 阶段
- Action → 行动
- Draw → 抽牌
- Play → 出牌/打出
- Discard → 弃牌
- Score → 计分
- Victory Point → 胜利点

### Player Count
- Solo → 单人
- Cooperative → 合作
- Team vs Team → 团队对抗
- Free-for-All → 自由对战
- 2-4 Players → 2-4名玩家

## Quality Assurance

1. **Completeness Check**: Every translatable text is translated
2. **Accuracy Check**: Technical terms are consistent with glossary
3. **Format Check**: Plain text output with newlines only

## Error Handling

| Error Type | Resolution |
|------------|------------|
| Unreadable image | Request higher quality image |
| Unsupported format | Convert or explain limitation |
| Missing sections | Flag for user clarification |
| Ambiguous terms | Provide context options |

## Usage Examples

### Example 1: PDF Upload
```
User: Please translate this board game rulebook
[Attaches: game_rules.pdf]
→ Process PDF → OCR if needed → Translate → Output markdown
```

### Example 2: Image Upload
```
User: Can you translate the rules from this photo?
[Attaches: rules_photo.jpg]
→ OCR image → Extract text → Translate → Output markdown
```

### Example 3: Text Paste
```
User: Translate this rule section:
"Each player draws 5 cards..."
→ Direct translation → Apply terminology → Output
```

## Reference Materials

- `references/board-game-terminology.md` - Complete terminology glossary
- `references/translation-rules.md` - Translation guidelines
- `references/common-phrases.md` - Standard phrase translations

## Limitations

- Complex diagrams and images require manual description
- Some region-specific jokes/cultural references may need adaptation
- Very low-resolution OCR may produce errors
