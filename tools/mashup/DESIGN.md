---
name: Mashup
description: A dense, source-forward editorial control surface for inspectable media edits.
colors:
  action-blue: "#2b4fd8"
  canvas: "#f4f5f7"
  surface: "#ffffff"
  surface-muted: "#eceef2"
  ink: "#16181d"
  ink-muted: "#5d626d"
  rule: "#d3d6dd"
  success: "#14663a"
  warning: "#8a5300"
  danger: "#b3261e"
typography:
  body:
    fontFamily: "system-ui, -apple-system, Segoe UI, Roboto, Helvetica Neue, Arial, sans-serif"
    fontSize: "13.5px"
    lineHeight: 1.45
  data:
    fontFamily: "ui-monospace, SFMono-Regular, SF Mono, Menlo, Consolas, monospace"
rounded:
  control: "5px"
  surface: "10px"
spacing:
  compact: "8px"
  standard: "16px"
components:
  button-primary:
    backgroundColor: "{colors.action-blue}"
    textColor: "{colors.surface}"
    rounded: "{rounded.control}"
  editor-card:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink}"
    rounded: "{rounded.control}"
---

# Design System: Mashup

## Overview

**Creative North Star: "The Editorial Control Desk"**

Mashup is a compact working surface, not a media showroom. Its visual system makes source material, timecodes, transcript evidence, scores, and edit state easy to compare while keeping familiar controls quiet. Blue is reserved for selection and primary action; provenance and operational state carry more visual weight than decoration.

**Key Characteristics:**

- Dense, scannable, and keyboard-first.
- Source-forward metadata paired with plain-language transcripts.
- Restrained surfaces with explicit semantic states.
- Responsive structure rather than fluid display typography.

## Colors

The palette uses cool neutral layers and one action blue, with green, amber, and red reserved for status.

**The One Action Color Rule.** Blue identifies selection, focus, and the primary action; it is not ambient decoration.

## Typography

System sans carries interface copy at a compact, stable scale. Monospace is limited to measurements, score values, identifiers, and source timecodes.

**The Evidence Type Rule.** Monospace means machine-verifiable editorial evidence, never generic technical flavor.

## Layout

Operational content sits in a centered 1080px workspace. Toolbars and metadata remain dense on desktop; candidate controls and evidence stack at content-driven breakpoints. Phone layouts preserve playback width and source attribution before secondary score detail.

### Public proof surface

The public surface extends the control desk into a screening sheet: the real
finished edit leads, its argument map and score ledger follow, and source rights
remain adjacent to the proof. Dark playback wells may create a focused viewing
zone, but the surrounding page keeps the same cool canvas, action blue, compact
rules, evidence type, and restrained geometry as the editor. It must never imply
that uploads, rendering, archive storage, or publishing are hosted services.

## Elevation & Depth

The system is flat by default. Borders and tonal surface changes establish hierarchy; shadows are not required for ordinary containers.

## Shapes

Controls and edit cards use compact 5px corners. Larger explanatory surfaces may use 10px corners, but pills are reserved for small status or measurement labels.

## Components

### Buttons

Buttons use familiar rectangular controls, visible hover and focus states, and direct action labels. One primary action per toolbar receives the action-blue fill.

### Cards / Containers

Edit candidates use a bordered surface, a numbered rail, a transcript body, and a compact evidence footer. Selection changes border and background tone without moving content.

### Status labels

Review state always combines text with semantic color. Color never carries approval state alone.

## Do's and Don'ts

### Do:

- **Do** show source title and original timecode beside every candidate.
- **Do** keep transcript text readable before exposing deeper scoring detail.
- **Do** use structural responsive changes for phone and tablet layouts.

### Don't:

- **Don't** turn the operator queue into a gallery of decorative media cards.
- **Don't** hide provenance, approval, or incomplete output behind optimistic status copy.
- **Don't** use motion unless it communicates an edit or review-state change.
