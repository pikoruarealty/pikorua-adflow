---
name: Aureate Editorial
colors:
  surface: '#faf9f7'
  surface-dim: '#dadad8'
  surface-bright: '#faf9f7'
  surface-container-lowest: '#ffffff'
  surface-container-low: '#f4f3f1'
  surface-container: '#efeeec'
  surface-container-high: '#e9e8e6'
  surface-container-highest: '#e3e2e0'
  on-surface: '#1a1c1b'
  on-surface-variant: '#4e4639'
  inverse-surface: '#2f3130'
  inverse-on-surface: '#f1f1ef'
  outline: '#7f7667'
  outline-variant: '#d1c5b4'
  surface-tint: '#775a19'
  primary: '#775a19'
  on-primary: '#ffffff'
  primary-container: '#c5a059'
  on-primary-container: '#4e3700'
  inverse-primary: '#e9c176'
  secondary: '#595e6d'
  on-secondary: '#ffffff'
  secondary-container: '#dbdff1'
  on-secondary-container: '#5d6272'
  tertiary: '#575e71'
  on-tertiary: '#ffffff'
  tertiary-container: '#9ea5ba'
  on-tertiary-container: '#343b4d'
  error: '#ba1a1a'
  on-error: '#ffffff'
  error-container: '#ffdad6'
  on-error-container: '#93000a'
  primary-fixed: '#ffdea5'
  primary-fixed-dim: '#e9c176'
  on-primary-fixed: '#261900'
  on-primary-fixed-variant: '#5d4201'
  secondary-fixed: '#dee2f4'
  secondary-fixed-dim: '#c2c6d8'
  on-secondary-fixed: '#161b28'
  on-secondary-fixed-variant: '#424655'
  tertiary-fixed: '#dbe2f9'
  tertiary-fixed-dim: '#bfc6dc'
  on-tertiary-fixed: '#141b2c'
  on-tertiary-fixed-variant: '#3f4759'
  background: '#faf9f7'
  on-background: '#1a1c1b'
  surface-variant: '#e3e2e0'
typography:
  display-lg:
    fontFamily: Playfair Display
    fontSize: 64px
    fontWeight: '700'
    lineHeight: '1.1'
    letterSpacing: -0.02em
  display-md:
    fontFamily: Playfair Display
    fontSize: 48px
    fontWeight: '600'
    lineHeight: '1.2'
    letterSpacing: -0.01em
  headline-lg:
    fontFamily: Playfair Display
    fontSize: 32px
    fontWeight: '600'
    lineHeight: '1.3'
  headline-lg-mobile:
    fontFamily: Playfair Display
    fontSize: 28px
    fontWeight: '600'
    lineHeight: '1.3'
  headline-md:
    fontFamily: Playfair Display
    fontSize: 24px
    fontWeight: '500'
    lineHeight: '1.4'
  body-lg:
    fontFamily: Inter
    fontSize: 18px
    fontWeight: '400'
    lineHeight: '1.6'
  body-md:
    fontFamily: Inter
    fontSize: 16px
    fontWeight: '400'
    lineHeight: '1.5'
  label-caps:
    fontFamily: Inter
    fontSize: 12px
    fontWeight: '600'
    lineHeight: '1'
    letterSpacing: 0.1em
  label-md:
    fontFamily: Inter
    fontSize: 14px
    fontWeight: '500'
    lineHeight: '1.2'
spacing:
  unit: 8px
  container-max: 1440px
  gutter: 32px
  margin-mobile: 20px
  margin-desktop: 64px
---

## Brand & Style
The design system is engineered for the high-end real estate market, positioning property listings as editorial features rather than database entries. The brand personality is **Confident, Calm, and Exclusive**, drawing inspiration from luxury publishing and high-fashion ateliers.

The visual direction utilizes a **Refined Editorial** style:
- **Minimalism:** Use of extreme negative space to create a sense of breathing room and exclusivity.
- **Sophistication:** High-contrast typography and a restrained color palette that recedes to prioritize high-fidelity architectural photography.
- **Precision:** Thin, hair-line borders and precise alignment mimic the layout of a physical luxury magazine.
- **Subtle Luxury:** Depth is achieved through material layers and soft, ambient shadows rather than loud decorative elements.

## Colors
The "Midnight & Gold" palette provides a timeless, high-contrast foundation. 

- **Primary (Gold):** Used sparingly for accents, primary actions, and brand-critical indicators. It signifies value and premium status.
- **Secondary (Deep Navy):** Acts as the core structural color for typography and dark-mode backgrounds.
- **Neutral (Ivory/Creme):** Replaces pure white for a softer, more "printed" feel on digital displays.
- **System Colors:** Success and Error states should be muted (e.g., Sage for success, Oxblood for error) to avoid breaking the editorial harmony.

## Typography
The typographic system creates a hierarchy between "Editorial Narratives" (Serif) and "Operational Interface" (Sans-Serif).

- **Playfair Display:** Reserved for property titles, section headers, and pull quotes. Use "optical sizing" where possible to maintain the hairline elegance of the serifs.
- **Inter:** Used for all UI controls, data points, and body copy. Its neutrality provides a professional contrast to the expressive serif.
- **Styling Note:** Use `label-caps` for small identifiers such as quality scores or property status to add a sense of authoritative branding.

## Layout & Spacing
The layout follows a **Fixed-Fluid Hybrid** model. Large desktop displays use a 12-column grid with generous 64px outer margins to maintain the "boutique" feel.

- **Vertical Rhythm:** Use a strict 8px baseline grid. Content sections should be separated by substantial white space (typically 80px, 120px, or 160px) to prevent the "dashboard" look.
- **Mobile Reflow:** On mobile, margins reduce to 20px. Grid columns collapse to a single column, but serif headers should retain significant size to preserve the editorial impact.
- **Composition:** Avoid center-aligning everything; utilize asymmetrical layouts where text blocks overlap image edges slightly to mimic high-end magazine spreads.

## Elevation & Depth
Depth is used to suggest "Paper on Surface" or "Glass on Photography."

- **Tonal Layers:** In light mode, use very subtle off-white (`#FDFDFB`) for cards against the Ivory background to create depth without shadows.
- **Ambient Shadows:** When shadows are required, they must be extremely diffused: `0px 20px 40px rgba(26, 31, 44, 0.04)`. They should feel like a soft glow rather than a hard drop.
- **Glassmorphism:** Use for overlays on property images (e.g., price tags or status badges). Apply a `20px` backdrop blur with a `15%` opacity white fill and a `0.5px` white border.

## Shapes
The design system employs **Sharp** (0px) edges to communicate architectural precision and modern luxury. 

- **Exceptions:** Very small UI elements like checkboxes or progress bar tracks may use a microscopic 2px radius for rendering clarity, but all primary containers, buttons, and image frames must remain strictly rectangular.
- **Borders:** Use thin, 1px lines in Gold or Deep Navy at 15% opacity for structural separation.

## Components

### Buttons & Inputs
- **Primary Button:** Solid Deep Navy or Gold with `label-caps` typography. No rounded corners. Heavy horizontal padding (32px+).
- **Ghost Button:** 1px border with high tracking (letter-spacing) on text.
- **Editable Text Blocks:** Display text should transform into a minimal input field with only a bottom border when focused. No background fill for inputs.

### Luxury-Themed Cards
- **The "Portfolio" Card:** Image-dominant with a 1px internal border inset by 16px. Text is placed in a "floating" glassmorphic container or directly on a creme base below the image.

### Quality Score Indicator (Scale of 10)
- Represented as a horizontal bar divided into 10 distinct segments. 
- Active segments are filled with Gold (#C5A059). 
- The score is displayed as a large Playfair Display number (e.g., "9.2") next to the bar.

### Multi-Stage Progress Bar
- Ultra-thin (2px) lines. 
- Completed stages are indicated by a change from Grey to Gold. 
- Stage markers are simple 8px vertical ticks rather than circles to maintain the architectural aesthetic.

### Lists & Tables
- Lists should have wide row heights (64px+) with thin 1px separators. 
- Avoid zebra-striping; use subtle hover states that change the background to a very light creme.