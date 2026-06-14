# NFDC Collections

Generates an iCalendar feed for refuse and recycling collections at The Hummicks, Beaulieu.

The generated calendar is `nfdc_bin_collections.ics`. It is refreshed weekly from the New Forest District Council form and projects the observed collection pattern three months ahead. Council-provided dates override projected dates when they appear in the published schedule window.

Once GitHub Pages is enabled for GitHub Actions, the subscription URL is:

```text
https://damorrison.github.io/NFDC-Collections/nfdc_bin_collections.ics
```

Calendar format:

- one combined event per collection day
- event titles include container emojis where supported by the calendar app
- 07:00 to 08:00 Europe/London
- display reminder 13 hours before collection
- area shown as `The Hummicks`

The update workflow can also be run manually from GitHub Actions.
