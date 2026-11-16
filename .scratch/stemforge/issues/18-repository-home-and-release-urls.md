# 18 — Repository home and release URLs

**Type:** task
**Status:** resolved
**Blocked by:** 10

## Question

Choose StemForge's permanent repository home and fill `packaging/release.toml`:

- Host and owner — a GitHub personal account, a project organisation, or another host. Repository name `stemforge`.
- `repo_url`, `releases_url`, and `source_offer_contact`.
- Whether the repository is public from the first commit. Ticket [13](13-licensing-and-attribution-compliance.md)'s GPLv3 §6(d) same-place offer and three-year written offer both assume a stable, public URL that outlives the release.
- Confirm the asset URL pattern `<releases_url>/tag/v<version>` and that release tags are immutable (never force-moved after publication).

Resolved when the host, owner, and URL pattern are recorded in this ticket's answer; the implementation effort then writes them into `packaging/release.toml`. The repository does not need to exist during this planning effort; the decision does, because the notices and `SOURCE_OFFER.md` cannot ship with a placeholder.

## Answer

**GitHub, owner `alexmarincu`, repository `stemforge`, public from the first commit.**

```toml
# packaging/release.toml
[repo]
url = "https://github.com/alexmarincu/stemforge"
releases_url = "https://github.com/alexmarincu/stemforge/releases"
source_offer_contact = "https://github.com/alexmarincu/stemforge/issues"
```

- **Human release page:** `https://github.com/alexmarincu/stemforge/releases/tag/v<version>`.
- **Asset download:** `https://github.com/alexmarincu/stemforge/releases/download/v<version>/<asset-name>` — this is the URL the source offer and the install instructions use, not the tag page.
- **Tags** are `v<major>.<minor>.<patch>` (matching `stemforge.__version__`) and are immutable once published: never force-move or delete a release tag, because the GPLv3 §6(d) same-place offer and the three-year written offer point at them.
- **Visibility:** the repository is public from the first commit. A private preparation period is acceptable only if the repository is public before the first published release; the notices cannot ship while the URL 404s for a recipient.
- **Current state:** created 2026-11-15 at `https://github.com/alexmarincu/stemforge` (`git@github.com:alexmarincu/stemforge.git`). It is public and empty; the first commit lands when the wayfinder map closes, at the start of the implementation effort. The `issues` tracker the source-offer contact points at exists with it.
- The notices generator, Inno script, and README read these three values from `packaging/release.toml`; no `<REPO_URL>`/`<RELEASE_URL>` or `CHANGEME` remains anywhere else, and `prepare` fails a tag if a placeholder reappears.

## Comments
