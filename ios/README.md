# OneAI iOS App (planned)

Thin SwiftUI client over the shared vault.

- Vault syncs via **iCloud Drive** (`~/Library/Mobile Documents/com~apple~CloudDocs/oneAI/vault`),
  visible in the Files app — no custom sync backend needed for v1.
- App features (v1):
  - Quick capture → writes `inbox/<timestamp>.md`
  - Task list → reads/writes `inbox/` notes with `status:` frontmatter
  - Draft review → approve/edit `inbox/drafts/*.md` before sending
- Later: CloudKit for structured sync, Apple Notes share extension.

The desktop daemon picks up anything dropped into `inbox/` automatically.
