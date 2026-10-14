# Privacy policy — Calciovich Video Analytics

*Last updated: 2026-10-14.*

**Calciovich Video Analytics** is a personal tool built and used by a single developer, Matteo Balducci, to read the statistics of the YouTube channels he owns and to chart them in his own dashboard. It is not offered to other users, and nobody other than the developer is expected to sign in to it.

## What the app accesses
When the developer signs in with his own Google accounts, the app requests **read-only** access to:

- the **YouTube Data API** (`youtube.readonly`): view, like and comment counts and the privacy status of videos on channels he owns;
- the **YouTube Analytics API** (`yt-analytics.readonly`): daily view counts per video on channels he owns.

It never asks for permission to upload, edit or delete anything from these OAuth clients.

## What is done with the data
The app stores aggregate performance counts (views, likes, comments, daily views) together with identifiers and metadata (such as video IDs, titles and privacy status) for the developer's own videos, in a private BigQuery dataset owned by the developer, and uses them to draw charts. The dataset itself is not shared. Aggregated statistics and video metadata may be displayed in a publicly accessible Looker Studio report. The data is not sold and is not used for advertising. No personal data about viewers, subscribers or commenters is collected or stored.

The use and transfer of information received from Google APIs adheres to the [Google API Services User Data Policy](https://developers.google.com/terms/api-services-user-data-policy), including the Limited Use requirements.

## Credentials
OAuth tokens are kept in the developer's private local storage and in encrypted GitHub repository secrets. Access can be revoked at any time at <https://myaccount.google.com/permissions>.

## Contact
Questions about this policy: matteo.balducci14@gmail.com
