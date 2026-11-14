# Visual guide

A picture of every look VideoSwami offers, made by VideoSwami itself on a generated demo clip (a presenter on a soft gradient). Under each picture is the command or edit spec that makes it.

Regenerate everything with `python3 tools/make_gallery.py`.

![Everything on one sheet](gallery/contact.jpg)

## Captions

Animated word-by-word captions, burned in. Each look is one `--style` value.

### pop

![captions pop](gallery/captions-pop.jpg)

```bash
python3 -m videoswami captions demo.mp4 --transcript demo.transcript.json --style pop -o out.mp4
```

### highlight

![captions highlight](gallery/captions-highlight.jpg)

```bash
python3 -m videoswami captions demo.mp4 --transcript demo.transcript.json --style highlight -o out.mp4
```

### karaoke

![captions karaoke](gallery/captions-karaoke.jpg)

```bash
python3 -m videoswami captions demo.mp4 --transcript demo.transcript.json --style karaoke -o out.mp4
```

### box

![captions box](gallery/captions-box.jpg)

```bash
python3 -m videoswami captions demo.mp4 --transcript demo.transcript.json --style box -o out.mp4
```

### classic

![captions classic](gallery/captions-classic.jpg)

```bash
python3 -m videoswami captions demo.mp4 --transcript demo.transcript.json --style classic -o out.mp4
```

### minimal

![captions minimal](gallery/captions-minimal.jpg)

```bash
python3 -m videoswami captions demo.mp4 --transcript demo.transcript.json --style minimal -o out.mp4
```

## Title templates

Ready-made animated titles and name tags. No image files needed.

### lower-third

![title lower-third](gallery/title-lower-third.jpg)

```bash
python3 -m videoswami title demo.mp4 --template lower-third --text "Alex Rivera" --sub "Video editor" --start 0.5 --end 5.5 -o out.mp4
```

### title-card

![title title-card](gallery/title-title-card.jpg)

```bash
python3 -m videoswami title demo.mp4 --template title-card --text "Summer Highlights" --sub "Made with VideoSwami" --start 0.5 --end 5.5 -o out.mp4
```

### corner-tag

![title corner-tag](gallery/title-corner-tag.jpg)

```bash
python3 -m videoswami title demo.mp4 --template corner-tag --text "LIVE" --start 0.5 --end 5.5 -o out.mp4
```

### end-card

![title end-card](gallery/title-end-card.jpg)

```bash
python3 -m videoswami title demo.mp4 --template end-card --text "Thanks for watching" --start 0.5 --end 5.5 -o out.mp4
```

### chapter

![title chapter](gallery/title-chapter.jpg)

```bash
python3 -m videoswami title demo.mp4 --template chapter --text "1" --sub "The setup" --start 0.5 --end 5.5 -o out.mp4
```

## Fitting wide video into a tall frame

A 16:9 clip placed in a 9:16 frame. `fit` decides what happens to the edges.

### contain

![fit contain](gallery/fit-contain.jpg)

Whole picture visible, empty bars (black here) above and below.

```json
{
  "output": {
    "width": 1080,
    "height": 1920
  },
  "timeline": [
    {
      "src": "demo.mp4",
      "fit": "contain"
    }
  ]
}
```

### cover

![fit cover](gallery/fit-cover.jpg)

Fills the frame; the sides are cut off.

```json
{
  "output": {
    "width": 1080,
    "height": 1920
  },
  "timeline": [
    {
      "src": "demo.mp4",
      "fit": "cover"
    }
  ]
}
```

### stretch

![fit stretch](gallery/fit-stretch.jpg)

Fills the frame by squashing the picture. Rarely what you want.

```json
{
  "output": {
    "width": 1080,
    "height": 1920
  },
  "timeline": [
    {
      "src": "demo.mp4",
      "fit": "stretch"
    }
  ]
}
```

### blur

![fit blur](gallery/fit-blur.jpg)

Whole picture visible over a blurred copy of itself.

```json
{
  "output": {
    "width": 1080,
    "height": 1920
  },
  "timeline": [
    {
      "src": "demo.mp4",
      "fit": "blur"
    }
  ]
}
```

## Transitions

The frame in the middle of a one-second transition between two clips. Any ffmpeg `xfade` name works.

### fade

![transition fade](gallery/transition-fade.jpg)

```json
{
  "timeline": [
    {
      "src": "a.mp4",
      "out": 3
    },
    {
      "src": "b.mp4",
      "out": 3,
      "transition": {
        "type": "fade",
        "duration": 1.0
      }
    }
  ]
}
```

### wipeleft

![transition wipeleft](gallery/transition-wipeleft.jpg)

```json
{
  "timeline": [
    {
      "src": "a.mp4",
      "out": 3
    },
    {
      "src": "b.mp4",
      "out": 3,
      "transition": {
        "type": "wipeleft",
        "duration": 1.0
      }
    }
  ]
}
```

### slideup

![transition slideup](gallery/transition-slideup.jpg)

```json
{
  "timeline": [
    {
      "src": "a.mp4",
      "out": 3
    },
    {
      "src": "b.mp4",
      "out": 3,
      "transition": {
        "type": "slideup",
        "duration": 1.0
      }
    }
  ]
}
```

### circleopen

![transition circleopen](gallery/transition-circleopen.jpg)

```json
{
  "timeline": [
    {
      "src": "a.mp4",
      "out": 3
    },
    {
      "src": "b.mp4",
      "out": 3,
      "transition": {
        "type": "circleopen",
        "duration": 1.0
      }
    }
  ]
}
```

### dissolve

![transition dissolve](gallery/transition-dissolve.jpg)

```json
{
  "timeline": [
    {
      "src": "a.mp4",
      "out": 3
    },
    {
      "src": "b.mp4",
      "out": 3,
      "transition": {
        "type": "dissolve",
        "duration": 1.0
      }
    }
  ]
}
```

## Text animation

The frame halfway through the text's entrance. Use `animate.in` and `animate.out` on any overlay.

### fade

![animate fade](gallery/animate-fade.jpg)

```json
{
  "timeline": [
    {
      "src": "demo.mp4"
    }
  ],
  "overlays": [
    {
      "type": "text",
      "text": "Animated text",
      "start": 1,
      "end": 5,
      "x": "center",
      "y": "center",
      "size": 96,
      "color": "white",
      "bold": true,
      "outline": 3,
      "animate": {
        "in": "fade",
        "duration": 1.0
      }
    }
  ]
}
```

### slide-up

![animate slide-up](gallery/animate-slide-up.jpg)

```json
{
  "timeline": [
    {
      "src": "demo.mp4"
    }
  ],
  "overlays": [
    {
      "type": "text",
      "text": "Animated text",
      "start": 1,
      "end": 5,
      "x": "center",
      "y": "center",
      "size": 96,
      "color": "white",
      "bold": true,
      "outline": 3,
      "animate": {
        "in": "slide-up",
        "duration": 1.0
      }
    }
  ]
}
```

### pop

![animate pop](gallery/animate-pop.jpg)

```json
{
  "timeline": [
    {
      "src": "demo.mp4"
    }
  ],
  "overlays": [
    {
      "type": "text",
      "text": "Animated text",
      "start": 1,
      "end": 5,
      "x": "center",
      "y": "center",
      "size": 96,
      "color": "white",
      "bold": true,
      "outline": 3,
      "animate": {
        "in": "pop",
        "duration": 1.0
      }
    }
  ]
}
```

## Looks and colour

Quick changes to how the picture feels.

### grayscale

![look grayscale](gallery/look-grayscale.jpg)

```bash
python3 -m videoswami color demo.mp4 --grayscale --contrast 1.3 -o out.mp4
```

### vignette

![look vignette](gallery/look-vignette.jpg)

```json
{
  "timeline": [
    {
      "src": "demo.mp4",
      "vignette": true
    }
  ]
}
```

### warm

![look warm](gallery/look-warm.jpg)

```bash
python3 -m videoswami color demo.mp4 --temperature 0.6 -o out.mp4
```

### cool

![look cool](gallery/look-cool.jpg)

```bash
python3 -m videoswami color demo.mp4 --temperature -0.6 -o out.mp4
```

### blur

![look blur](gallery/look-blur.jpg)

```json
{
  "timeline": [
    {
      "src": "demo.mp4",
      "blur": 20
    }
  ]
}
```

### sharpen

![look sharpen](gallery/look-sharpen.jpg)

```json
{
  "timeline": [
    {
      "src": "demo.mp4",
      "sharpen": 1.5
    }
  ]
}
```

## Export profiles

One picture per platform preset, showing the shape of the finished video.

### reels

![profile reels](gallery/profile-reels.jpg)

1080x1920, 30 fps, loudness normalised.

```bash
python3 -m videoswami export demo.mp4 --profile reels -o out.mp4
```

### shorts

![profile shorts](gallery/profile-shorts.jpg)

1080x1920, 30 fps, loudness normalised.

```bash
python3 -m videoswami export demo.mp4 --profile shorts -o out.mp4
```

### tiktok

![profile tiktok](gallery/profile-tiktok.jpg)

1080x1920, 30 fps, loudness normalised.

```bash
python3 -m videoswami export demo.mp4 --profile tiktok -o out.mp4
```

### youtube

![profile youtube](gallery/profile-youtube.jpg)

1920x1080.

```bash
python3 -m videoswami export demo.mp4 --profile youtube -o out.mp4
```

### youtube-4k

![profile youtube-4k](gallery/profile-youtube-4k.jpg)

3840x2160.

```bash
python3 -m videoswami export demo.mp4 --profile youtube-4k -o out.mp4
```

### square

![profile square](gallery/profile-square.jpg)

1080x1080.

```bash
python3 -m videoswami export demo.mp4 --profile square -o out.mp4
```

### portrait

![profile portrait](gallery/profile-portrait.jpg)

1080x1350.

```bash
python3 -m videoswami export demo.mp4 --profile portrait -o out.mp4
```

### whatsapp

![profile whatsapp](gallery/profile-whatsapp.jpg)

1280x720 at most, small file.

```bash
python3 -m videoswami export demo.mp4 --profile whatsapp -o out.mp4
```

### x

![profile x](gallery/profile-x.jpg)

1280x720.

```bash
python3 -m videoswami export demo.mp4 --profile x -o out.mp4
```
