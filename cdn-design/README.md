# Images CDN · Exercise 2 (system design)

Two files with no build step. Open them in a browser; they load the IBM Plex fonts from Google Fonts when online and fall back to system fonts otherwise.

**Deliverable: [`CDN-Design.html`](CDN-Design.html).** The design, in a ten-minute read. It is enough to assess the design on its own.

1. The summary box at the top: the design in five decisions.
2. §1: each line of the brief mapped to the mechanism that meets it.
3. §2: the three flows with figures 1–3 (upload, read on a miss, resync).
4. §3: the architecture diagram.
5. §4–§6: sizing, failures, and the tools deliberately left out.

**Supporting material: [`CDN-Design-Details.html`](CDN-Design-Details.html).** The reasoning behind each section. §2–§6 expand the section of the same number in the design; §7–§10 cover the ID scheme, operations, the API and the test plan. §2–§6 of the design each link to their counterpart.

The brief is Exercise 2 of [the assessment PDF](<../Hiring-Excercices for the candidate as Backend Developer-110324-120530.pdf>) at the repository root. Exercise 1, the URL shortener, is in [`../url-shortener`](../url-shortener/).
