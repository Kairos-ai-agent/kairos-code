---
name: algorithmic-art
description: Creating algorithmic art using p5.js with seeded randomness and interactive parameter exploration. Use this when users request creating art using code, generative art, algorithmic art, flow fields, or particle systems. Create original algorithmic art rather than copying existing artists' work to avoid copyright violations.
priority: 0.7
when:
  keyword: ['creating', 'algorithmic', 'art', 'using', 'seeded', 'randomness']
---

Algorithmic philosophies are computational aesthetic movements that are then expressed through code. Output .md files (philosophy), .html files (interactive viewer), and .js files (generative algorithms).

This happens in two steps:
1. Algorithmic Philosophy Creation (.md file)
2. Express by creating p5.js generative art (.html + .js files)

First, undertake this task:

## ALGORITHMIC PHILOSOPHY CREATION

To begin, create an ALGORITHMIC PHILOSOPHY (not static images or templates) that will be interpreted through:
- Computational processes, emergent behavior, mathematical beauty
- Seeded randomness, noise fields, organic systems
- Particles, flows, fields, forces
- Parametric variation and controlled chaos

### THE CRITICAL UNDERSTANDING
- What is received: Some subtle input or instructions by the user to take into account, but use as a foundation; it should not constrain creative freedom.
- What is created: An algorithmic philosophy/generative aesthetic movement.
- What happens next: The same version receives the philosophy and EXPRESSES IT IN CODE - creating p5.js sketches that are 90% algorithmic generation, 10% essential parameters.

Consider this approach:
- Write a manifesto for a generative art movement
- The next phase involves writing the algorithm that brings it to life

The philosophy must emphasize: Algorithmic expression. Emergent behavior. Computational beauty. Seeded variation.

### HOW TO GENERATE AN ALGORITHMIC PHILOSOPHY

**Name the movement** (1-2 words): "Organic Turbulence" / "Quantum Harmonics" / "Emergent Stillness"

**Articulate the philosophy** (4-6 paragraphs - concise but complete):

To capture the ALGORITHMIC essence, express how this philosophy manifests through:
- Computational processes and mathematical relationships?
- Noise functions and randomness patterns?
- Particle behaviors and field dynamics?
- Temporal evolution and system states?
- Parametric variation and emergent complexity?

**CRITICAL GUIDELINES:**
- **Avoid redundancy**: Each algorithmic aspect should be mentioned once.
- **Emphasize craftsmanship REPEATEDLY**: The philosophy MUST stress multiple times that the final algorithm should appear as though it took countless hours to develop, was refined with care, and comes from someone at the absolute top of their field.
- **Leave creative space**: Be specific about the algorithmic direction, but concise enough that the next Claude has room to make interpretive implementation choices at an extremely high level of craftsmanship.

### PHILOSOPHY EXAMPLES

**"Organic Turbulence"**: Chaos constrained by natural law, order emerging from disorder. Flow fields driven by layered Perlin noise. Thousands of particles following vector forces, their trails accumulating into organic density maps.

**"Quantum Harmonics"**: Discrete entities exhibiting wave-like interference patterns. Particles initialized on a grid, each carrying a phase value that evolves through sine waves.

**"Recursive Whispers"**: Self-similarity across scales, infinite depth in finite space. Branching structures that subdivide recursively. L-systems or recursive subdivision generate tree-like forms.

**"Field Dynamics"**: Invisible forces made visible through their effects on matter. Vector fields constructed from mathematical functions or noise.

**"Stochastic Crystallization"**: Random processes crystallizing into ordered structures. Randomized circle packing or Voronoi tessellation.

### ESSENTIAL PRINCIPLES
- **ALGORITHMIC PHILOSOPHY**: Creating a computational worldview to be expressed through code
- **PROCESS OVER PRODUCT**: Beauty emerges from the algorithm's execution
- **PARAMETRIC EXPRESSION**: Ideas communicate through mathematical relationships
- **ARTISTIC FREEDOM**: The next Claude interprets the philosophy algorithmically
- **PURE GENERATIVE ART**: This is about making LIVING ALGORITHMS
- **EXPERT CRAFTSMANSHIP**: Final algorithm must feel meticulously crafted

**The algorithmic philosophy should be 4-6 paragraphs long.** Fill it with poetic computational philosophy that brings together the intended vision.

---

## P5.JS IMPLEMENTATION

With the philosophy AND conceptual framework established, express it through code.

### TECHNICAL REQUIREMENTS

**Seeded Randomness (Art Blocks Pattern)**:
```javascript
let seed = 12345;
randomSeed(seed);
noiseSeed(seed);
```

**Parameter Structure - FOLLOW THE PHILOSOPHY**:
```javascript
let params = {
  seed: 12345,
  // colors
  // quantities (how many?)
  // scales (how big? how fast?)
  // probabilities (how likely?)
  // ratios (what proportions?)
  // angles (what direction?)
  // thresholds (when does behavior change?)
};
```

**Canvas Setup**: Standard p5.js structure:
```javascript
function setup() {
  createCanvas(1200, 1200);
}

function draw() {
  // Your generative algorithm
}
```

### CRAFTSMANSHIP REQUIREMENTS

- **Balance**: Complexity without visual noise, order without rigidity
- **Color Harmony**: Thoughtful palettes, not random RGB values
- **Composition**: Even in randomness, maintain visual hierarchy
- **Performance**: Smooth execution
- **Reproducibility**: Same seed ALWAYS produces identical output

### OUTPUT FORMAT

Output:
1. **Algorithmic Philosophy** - As markdown or text
2. **Single HTML Artifact** - Self-contained interactive generative art

The HTML artifact contains everything: p5.js (from CDN), the algorithm, parameter controls, and UI - all in one file that works immediately in any browser.

---

## REQUIRED FEATURES

**1. Parameter Controls**
- Sliders for numeric parameters
- Color pickers for palette colors
- Real-time updates when parameters change
- Reset button to restore defaults

**2. Seed Navigation**
- Display current seed number
- "Previous" and "Next" buttons to cycle through seeds
- "Random" button for random seed
- Input field to jump to specific seed

**3. Single Artifact Structure**
```html
<!DOCTYPE html>
<html>
<head>
  <script src="https://cdnjs.cloudflare.com/ajax/libs/p5.js/1.7.0/p5.min.js"></script>
</head>
<body>
  <div id="canvas-container"></div>
  <div id="controls">
    <!-- All parameter controls -->
  </div>
  <script>
    // ALL p5.js code inline here
    // Parameter objects, classes, functions
    // setup() and draw()
  </script>
</body>
</html>
```

**Requirements**:
- Seed controls must work (prev/next/random/jump/display)
- All parameters must have UI controls
- Regenerate, Reset buttons must work


<!--
Adapted from anthropics/skills (Apache-2.0).
Upstream: https://github.com/anthropics/skills/tree/main/skills/algorithmic-art
Adaptation: scripts/adapt_anthropic_skills.py
-->
