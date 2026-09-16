---
name: "suika-fruit-merge-game"
description: "Creates Suika Game-style fruit merge games. Trigger phrases: \"make a merge game\", \"Suika Game\", \"fruit merge\", \"合成大西瓜\", \"连连看游戏\", \"做个合成游戏\"."
priority: 0.5
imported-from: "minimax"
source-path: "C:\\Users\\leohu\\.minimax\\skills\\suika-fruit-merge-game\\SKILL.md"
---
# 西瓜合成游戏生成器

## 概述

生成类似「合成大西瓜」的水果合成游戏，支持物理碰撞、合成升级、分数系统和多种水果类型。

## 游戏核心规则

### 水果等级（11级）

| 等级 | 水果 | 颜色 | 大小 | 合成后 |
|------|------|------|------|--------|
| 1 | 樱桃 | #e74c3c | 30px | 葡萄 |
| 2 | 葡萄 | #9b59b6 | 40px | 橘子 |
| 3 | 橘子 | #f39c12 | 50px | 柠檬 |
| 4 | 柠檬 | #f1c40f | 60px | 苹果 |
| 5 | 苹果 | #27ae60 | 75px | 梨子 |
| 6 | 梨子 | #1abc9c | 90px | 桃子 |
| 7 | 桃子 | #ff9ff3 | 105px | 菠萝 |
| 8 | 菠萝 | #ffeaa7 | 120px | 椰子 |
| 9 | 椰子 | #dfe6e9 | 140px | 哈密瓜 |
| 10 | 哈密瓜 | #a29bfe | 165px | 西瓜 |
| 11 | 西瓜 | #ff6b6b | 190px | 生成新西瓜 |

### 计分规则

- 每合成一次得 (等级 × 10) 分
- 最高级西瓜奖励 500 分

---

## 游戏实现模板

### HTML 结构

```html
<!DOCTYPE html>
<html>
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>🍉 合成大西瓜</title>
  <style>
    * { margin: 0; padding: 0; box-sizing: border-box; }
    body {
      font-family: 'Segoe UI', Arial, sans-serif;
      background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
      min-height: 100vh;
      display: flex;
      flex-direction: column;
      align-items: center;
      padding: 20px;
    }
    .game-title {
      color: white;
      font-size: 2em;
      margin-bottom: 10px;
      text-shadow: 2px 2px 4px rgba(0,0,0,0.3);
    }
    .score-board {
      background: rgba(255,255,255,0.95);
      padding: 10px 30px;
      border-radius: 25px;
      margin-bottom: 20px;
      box-shadow: 0 4px 15px rgba(0,0,0,0.2);
    }
    .score-board span {
      font-size: 1.5em;
      font-weight: bold;
      color: #333;
    }
    .game-container {
      position: relative;
      width: 400px;
      height: 600px;
      background: rgba(255,255,255,0.9);
      border-radius: 15px;
      overflow: hidden;
      box-shadow: 0 10px 40px rgba(0,0,0,0.3);
    }
    .next-fruit {
      position: absolute;
      top: 10px;
      right: 10px;
      background: rgba(0,0,0,0.1);
      border-radius: 10px;
      padding: 10px;
      text-align: center;
    }
    .next-fruit-label {
      font-size: 0.8em;
      color: #666;
    }
    #game-canvas {
      display: block;
    }
    .drop-zone {
      position: absolute;
      top: 0;
      left: 0;
      right: 0;
      height: 50px;
      background: linear-gradient(to bottom, rgba(0,0,0,0.2), transparent);
      cursor: pointer;
    }
    .drop-zone:hover { background: linear-gradient(to bottom, rgba(0,0,0,0.3), transparent); }
    .instructions {
      color: white;
      margin-top: 20px;
      text-align: center;
      opacity: 0.9;
    }
    .game-over-overlay {
      position: absolute;
      top: 0; left: 0; right: 0; bottom: 0;
      background: rgba(0,0,0,0.8);
      display: none;
      flex-direction: column;
      justify-content: center;
      align-items: center;
      color: white;
    }
    .game-over-overlay.show { display: flex; }
    .game-over-text { font-size: 2em; margin-bottom: 20px; }
    .restart-btn {
      padding: 15px 40px;
      font-size: 1.2em;
      background: #27ae60;
      color: white;
      border: none;
      border-radius: 25px;
      cursor: pointer;
      transition: transform 0.2s, background 0.2s;
    }
    .restart-btn:hover { background: #2ecc71; transform: scale(1.05); }
  </style>
</head>
<body>
  <h1 class="game-title">🍉 合成大西瓜</h1>
  <div class="score-board">分数: <span id="score">0</span></div>
  <div class="game-container">
    <div class="drop-zone" id="drop-zone"></div>
    <div class="next-fruit">
      <div class="next-fruit-label">下一个</div>
      <canvas id="next-canvas" width="60" height="60"></canvas>
    </div>
    <canvas id="game-canvas" width="400" height="600"></canvas>
    <div class="game-over-overlay" id="game-over">
      <div class="game-over-text">游戏结束！</div>
      <div id="final-score">最终分数: 0</div>
      <button class="restart-btn" onclick="restartGame()">再来一局</button>
    </div>
  </div>
  <p class="instructions">点击上方区域投放水果，相同水果碰撞会合成更高级的水果！</p>

  <script>
    // 水果配置
    const FRUITS = [
      { name: '樱桃', emoji: '🍒', color: '#e74c3c', radius: 15 },
      { name: '葡萄', emoji: '🍇', color: '#9b59b6', radius: 20 },
      { name: '橘子', emoji: '🍊', color: '#f39c12', radius: 25 },
      { name: '柠檬', emoji: '🍋', color: '#f1c40f', radius: 30 },
      { name: '苹果', emoji: '🍎', color: '#e74c3c', radius: 37 },
      { name: '梨子', emoji: '🍐', color: '#a8e6cf', radius: 45 },
      { name: '桃子', emoji: '🍑', color: '#ff9ff3', radius: 52 },
      { name: '菠萝', emoji: '🍍', color: '#ffeaa7', radius: 60 },
      { name: '椰子', emoji: '🥥', color: '#dfe6e9', radius: 70 },
      { name: '哈密瓜', emoji: '🍈', color: '#a29bfe', radius: 82 },
      { name: '西瓜', emoji: '🍉', color: '#ff6b6b', radius: 95 }
    ];

    const canvas = document.getElementById('game-canvas');
    const ctx = canvas.getContext('2d');
    const nextCanvas = document.getElementById('next-canvas');
    const nextCtx = nextCanvas.getContext('2d');
    const dropZone = document.getElementById('drop-zone');

    let fruits = [];
    let score = 0;
    let nextFruitIndex = 0;
    let currentFruitIndex = 0;
    let gameOver = false;
    let mouseX = 200;
    let canDrop = true;
    let lastDropTime = 0;

    // 物理参数
    const GRAVITY = 0.3;
    const FRICTION = 0.99;
    const BOUNCE = 0.6;

    class Fruit {
      constructor(x, y, index) {
        this.x = x;
        this.y = y;
        this.index = index;
        this.vx = 0;
        this.vy = 0;
        this.radius = FRUITS[index].radius;
        this.merged = false;
      }

      update() {
        this.vy += GRAVITY;
        this.vx *= FRICTION;
        this.x += this.vx;
        this.y += this.vy;

        // 边界碰撞
        if (this.x - this.radius < 0) {
          this.x = this.radius;
          this.vx *= -BOUNCE;
        }
        if (this.x + this.radius > canvas.width) {
          this.x = canvas.width - this.radius;
          this.vx *= -BOUNCE;
        }
        if (this.y + this.radius > canvas.height) {
          this.y = canvas.height - this.radius;
          this.vy *= -BOUNCE;
          if (Math.abs(this.vy) < 1) this.vy = 0;
        }
      }

      draw(context) {
        const fruit = FRUITS[this.index];
        context.beginPath();
        context.arc(this.x, this.y, this.radius, 0, Math.PI * 2);

        // 渐变填充
        const gradient = context.createRadialGradient(
          this.x - this.radius * 0.3, this.y - this.radius * 0.3, 0,
          this.x, this.y, this.radius
        );
        gradient.addColorStop(0, this.lightenColor(fruit.color, 30));
        gradient.addColorStop(1, fruit.color);
        context.fillStyle = gradient;
        context.fill();

        // 阴影
        context.shadowColor = 'rgba(0,0,0,0.3)';
        context.shadowBlur = 10;
        context.shadowOffsetY = 5;
        context.fill();
        context.shadowColor = 'transparent';

        // 表情
        context.font = `${this.radius}px Arial`;
        context.textAlign = 'center';
        context.textBaseline = 'middle';
        context.fillText(fruit.emoji, this.x, this.y);
      }

      lightenColor(color, percent) {
        const num = parseInt(color.replace('#', ''), 16);
        const amt = Math.round(2.55 * percent);
        const R = Math.min(255, (num >> 16) + amt);
        const G = Math.min(255, ((num >> 8) & 0x00FF) + amt);
        const B = Math.min(255, (num & 0x0000FF) + amt);
        return '#' + (0x1000000 + R * 0x10000 + G * 0x100 + B).toString(16).slice(1);
      }
    }

    function generateNextFruit() {
      // 只生成前5种水果
      return Math.floor(Math.random() * 5);
    }

    function drawNextFruit() {
      nextCtx.clearRect(0, 0, 60, 60);
      const fruit = FRUITS[nextFruitIndex];
      nextCtx.font = '40px Arial';
      nextCtx.textAlign = 'center';
      nextCtx.textBaseline = 'middle';
      nextCtx.fillText(fruit.emoji, 30, 30);
    }

    function checkCollision(f1, f2) {
      const dx = f1.x - f2.x;
      const dy = f1.y - f2.y;
      const distance = Math.sqrt(dx * dx + dy * dy);
      return distance < f1.radius + f2.radius;
    }

    function handleMerging() {
      for (let i = 0; i < fruits.length; i++) {
        for (let j = i + 1; j < fruits.length; j++) {
          if (fruits[i].merged || fruits[j].merged) continue;

          if (checkCollision(fruits[i], fruits[j])) {
            if (fruits[i].index === fruits[j].index) {
              // 相同水果，合成
              fruits[i].merged = true;
              fruits[j].merged = true;

              const newIndex = Math.min(fruits[i].index + 1, FRUITS.length - 1);
              const newX = (fruits[i].x + fruits[j].x) / 2;
              const newY = (fruits[i].y + fruits[j].y) / 2;

              fruits.push(new Fruit(newX, newY, newIndex));
              score += (fruits[i].index + 1) * 10;
              document.getElementById('score').textContent = score;

              fruits.splice(i, 1);
              fruits.splice(j - 1, 1);
              return true;
            }
          }
        }
      }
      return false;
    }

    function drawPreview() {
      if (canDrop && !gameOver) {
        ctx.save();
        ctx.globalAlpha = 0.5;
        const previewFruit = new Fruit(mouseX, 30, currentFruitIndex);
        previewFruit.draw(ctx);
        ctx.restore();

        // 绘制下落引导线
        ctx.setLineDash([5, 5]);
        ctx.strokeStyle = 'rgba(0,0,0,0.2)';
        ctx.beginPath();
        ctx.moveTo(mouseX, 50);
        ctx.lineTo(mouseX, canvas.height);
        ctx.stroke();
        ctx.setLineDash([]);
      }
    }

    function gameLoop() {
      ctx.clearRect(0, 0, canvas.width, canvas.height);

      // 更新水果
      fruits.forEach(fruit => fruit.update());

      // 移除已合并的水果
      fruits = fruits.filter(fruit => !fruit.merged);

      // 检查合成
      handleMerging();

      // 绘制水果
      fruits.forEach(fruit => fruit.draw(ctx));

      // 绘制预览
      drawPreview();

      // 检查游戏结束（水果堆积到顶部）
      const topFruits = fruits.filter(f => f.y < 60 && Math.abs(f.vy) < 1);
      if (topFruits.length > 3 && !gameOver) {
        gameOver = true;
        document.getElementById('game-over').classList.add('show');
        document.getElementById('final-score').textContent = `最终分数: ${score}`;
      }

      requestAnimationFrame(gameLoop);
    }

    dropZone.addEventListener('mousemove', (e) => {
      const rect = canvas.getBoundingClientRect();
      mouseX = e.clientX - rect.left;
      // 限制水果位置在有效范围内
      const minX = FRUITS[currentFruitIndex].radius;
      const maxX = canvas.width - FRUITS[currentFruitIndex].radius;
      mouseX = Math.max(minX, Math.min(maxX, mouseX));
    });

    dropZone.addEventListener('click', () => {
      if (canDrop && !gameOver) {
        const now = Date.now();
        if (now - lastDropTime > 300) {
          fruits.push(new Fruit(mouseX, 30, currentFruitIndex));
          currentFruitIndex = nextFruitIndex;
          nextFruitIndex = generateNextFruit();
          drawNextFruit();
          lastDropTime = now;

          // 短暂冷却
          canDrop = false;
          setTimeout(() => canDrop = true, 300);
        }
      }
    });

    function restartGame() {
      fruits = [];
      score = 0;
      gameOver = false;
      document.getElementById('score').textContent = '0';
      document.getElementById('game-over').classList.remove('show');
      currentFruitIndex = generateNextFruit();
      nextFruitIndex = generateNextFruit();
      drawNextFruit();
    }

    // 初始化游戏
    currentFruitIndex = generateNextFruit();
    nextFruitIndex = generateNextFruit();
    drawNextFruit();
    gameLoop();
  </script>
</body>
</html>
```

---

## 水果配置参考表

```javascript
const FRUITS = [
  { name: '樱桃', emoji: '🍒', color: '#e74c3c', radius: 15 },
  { name: '葡萄', emoji: '🍇', color: '#9b59b6', radius: 20 },
  { name: '橘子', emoji: '🍊', color: '#f39c12', radius: 25 },
  { name: '柠檬', emoji: '🍋', color: '#f1c40f', radius: 30 },
  { name: '苹果', emoji: '🍎', color: '#27ae60', radius: 37 },
  { name: '梨子', emoji: '🍐', color: '#a8e6cf', radius: 45 },
  { name: '桃子', emoji: '🍑', color: '#ff9ff3', radius: 52 },
  { name: '菠萝', emoji: '🍍', color: '#ffeaa7', radius: 60 },
  { name: '椰子', emoji: '🥥', color: '#dfe6e9', radius: 70 },
  { name: '哈密瓜', emoji: '🍈', color: '#a29bfe', radius: 82 },
  { name: '西瓜', emoji: '🍉', color: '#ff6b6b', radius: 95 }
];
```

---

## 触发条件检查

- 用户请求制作合成类游戏
- 用户提到"合成大西瓜"、"Suika"、"连连看"关键词
- 用户想要物理碰撞类休闲游戏

---

## 输出要求

1. 生成完整的 HTML 游戏代码
2. 包含所有水果配置
3. 实现物理碰撞和合成逻辑
4. 添加分数系统和游戏结束判定
5. 支持移动端触摸操作
