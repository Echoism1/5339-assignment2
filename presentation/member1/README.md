# Member 1 presentation

Open `index.html` in a current Chrome or Edge browser. The presentation has
nine English slides and Chinese speaker notes, planned for 4 minutes 18 seconds
within a five-minute slot. The image, Inter font, icons and animation code are
embedded, so the HTML can be copied and opened on its own without Internet access.

- Use Left / Right to change slides, Home / End to jump, and Escape for the overview.
- Press P for the presenter view and allow the audience window to open. Put that
  window on the projector; keep the notes window on your own screen.
- Start the timer manually. Use the presenter controls for notes, rehearsal and
  audience recovery. In normal slide view, B toggles static mode; in presenter
  view, B blacks out the audience screen.

The source image is `images/03-dc-charging.png`. The copy inside the HTML is
embedded; replacing the PNG alone does not change the presentation.
The generation prompt is in `images/03-dc-charging.prompt.txt`.
`preview.png` shows all nine slides at a glance.
`assets/motion.min.js` is the template's local animation source copy.

Content comes from `prepare_data.py`, the supplied generator, `build_events.py`,
`publisher.py` and the included CSV / JSON data. These files are read only when
preparing the slides; the presentation does not start the application or replay MQTT.
