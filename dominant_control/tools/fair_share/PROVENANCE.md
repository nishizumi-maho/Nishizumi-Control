# Fair Share provenance

The logic of this integration was ported from the MIT project
`nishizumi-maho/Nishizumi-Calculator`, revision
`c3d8a1ccc395707519b6f375aaa9b8562180c331` (2026-01-08).

Rules kept as they were:

- equal split: total laps / drivers, rounded up;
- minimum fair share: 25% of the equal split, rounded up;
- when the total number of laps is left out, it is estimated from
  duration / average lap;
- times are estimated by multiplying the laps by the average lap.

The presentation was rewritten in Tkinter so it runs natively and offline
inside Dominant Control. The original license is in
`licenses/Nishizumi-Calculator-LICENSE.txt`.
