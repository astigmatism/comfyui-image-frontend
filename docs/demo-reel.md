# LoRA Demo Reels

Each LoRA card's **Demo Reel** control opens its shared gallery above the picker.
That gallery is the main viewport's grouped view: one collapsible group per prompt
slot, each headed by the slot's title, a rule drawn across, and how many images it
holds — the slot's own prompt stays available as a tooltip rather than as a line of
text. Inside each group sit the gallery's own cards, whose controls — details,
download, favorite, recall, re-run, copy, move, regenerate, and delete — are
revealed on hover and never written underneath the image. Paging follows the
viewport too, so a long reel loads as it is scrolled. Beside the dialog's close
button, an administrator is offered one icon: **Edit Demo Reel**. Retrying failed
images and pausing the background worker are the business of the process that
produces them, so neither is offered while browsing the results; pausing remains
available where the reel is configured. Returning preserves uncommitted LoRA
selections and scroll position. Images use the normal photo viewer. Reel
collections stay outside normal navigation.

## Setup

Administrators choose **Demo Reel settings** in the LoRA picker header, beside its
close button. The editor is also available inside each reel. Opened from a LoRA it
additionally offers that LoRA's own strength override inside the family card whose
strength it replaces, named for the LoRA it applies to.

1. Add shared prompt slots. A slot's optional title is its heading. Literal
   `{subject}` expands to each LoRA's published trigger phrase, falling back to its
   published title.
2. Reorder slots with the heading's drag handle, arrow keys, or buttons; Duplicate
   copies a slot in place.
3. Leave a slot's **Image size** empty to inherit the family size, or give it its
   own width and height.
4. Open **Model families** to enable a family and choose its workflow, LoRA
   strength, and workflow settings. Those settings are the workflow's own controls
   with their published descriptions and Basic/Advanced split; a value that
   differs from the workflow default is marked and can be restored on its own or
   reset for the whole family. Individual LoRAs can override strength.
5. Choose whose checkpoint grades qualify models. Initial setup uses the current
   administrator's account. Only that account's A/B grades affect eligibility.
6. Save. These changes are independent of selection Apply/Cancel. Canceling the
   picker does not undo saved Demo Reel settings.

Recipes require a published seed control and cannot require uploaded images.
Every new attempt randomizes its seed, including workflows whose normal default
is fixed. Only the target selectable LoRA is enabled, and each image's checkpoint
comes from the rating account's A/B grades; the family card lists those three as
things the Demo Reel decides rather than offering settings that it overwrites.
Prompts run directly after
subject substitution, without Prompt Generation or Creative Direction. The
configured workflow is exclusive; eligible checkpoints exposed by another library
workflow but missing from that workflow appear as blocked.

## Idle generation and cancellation

The server waits five minutes after interaction and user work across all accounts.
Entry, foregrounding, navigation, pointer, keyboard, touch, and scrolling input
count. Passive polling, SSE, image loading, and programmatic scrolling do not.
Browsers immediately report the first interaction and coalesce continued input.

One background image runs globally. Order follows model families, LoRA gallery
order, slots, then A and B checkpoints in the rating account's saved order. Failed
or blocked combinations do not hold up other work. Image failures receive three
attempts with one-minute and five-minute retry waits. Unavailable workers wait
without accepting an attempt. Exhausted entries are retried through the reel's
`retry` action, and pausing is offered in **Demo Reel settings**: neither is a
browsing concern, so the reel itself offers no button for either.

Activity durably revokes an in-progress attempt before requesting safe targeted
ComfyUI cancellation. Navigation does not wait for interruption. Late or partial
output is discarded and cleaned up. Replacement attempts receive fresh random
seeds. Already completed images survive returning to the application. Restart
discards unfinished attempts and restarts the five-minute quiet period.

## Actions and invalidation

Signed-in users can download, privately favorite, copy, recall, and re-run prompts.
Copies and re-runs belong to the acting user. Administrators can additionally
delete, move, or regenerate shared results. Move copies into a personal collection
before removing the shared result. Transfer receipts prevent duplicate copies
after lost responses, and failures roll back the destination. Personal copies
own independent files and survive reel invalidation.

Delete, move, and regenerate leave missing combinations for the next idle period.
Prompt/resolution changes invalidate affected slots; recipe changes invalidate the
family; strength or expanded-subject changes invalidate that LoRA. Removed LoRAs,
removed checkpoints, and checkpoints downgraded below B lose their results.
Promotions into A/B add missing work. Slot titles/order, tier order, and A↔B moves
preserve results.

Compatible catalog additions and descriptive changes preserve images. Executable
workflow changes invalidate affected results and require recipe review and Save.
If the rating account disappears, generation blocks and existing images remain
available until an administrator chooses a replacement account.

## LoRA display picture

The library picture of a LoRA is one of these images: the earliest image any signed-in
account has favorited in the reel's **first prompt slot**, or, while nothing in that
slot is favorited, the slot's own first image — slot order, then the rating account's A
then B checkpoint order. A later slot never supplies it, and making another slot first
moves the picture to that slot.

Nothing is uploaded or deleted by hand. A picture appears as soon as the first slot
holds an image, follows a favorite the moment it is favorited or removed, and goes away
when that image is deleted or invalidated, when the LoRA leaves the library, or when the
first slot is emptied. Favorites stay private to each account; the picture they choose
is shared, because a LoRA's picture belongs to its shared identity.

The LoRA gallery draws a LoRA as a card while it has a picture and as a compact row
while it does not. Reconciling pictures is presentation work: it runs after reel
reconciliation on the idle loop, rewrites only LoRAs whose source image actually
changed, and never interrupts or fails reel work. See
[LoRA display images](api.md#shared-lora-images).

## Storage and access

Migration `c43d89e12a70` adds `users.is_system`, `demo_reel_config`, `demo_reels`,
`demo_reel_entries`, and `demo_reel_transfers`. Configuration stores ordered slots,
family recipes and overrides as revision-checked JSON. Entries uniquely identify
each reel/slot/checkpoint combination, with durable failure/backoff state.
Migration `7d1a4c8e5b32` adds the two columns by which a LoRA's display image names
the reel image it was made from; that row is a cache rebuilt from these entries and
never something the reel itself depends on.

Generations, hidden collections, and artifacts use a protected internal account.
It cannot sign in, be reset/deleted, or appear in account-management choices.
Once initialized, downgrade requires backup recovery instead of silently dropping
system-owned content. Shared APIs verify current membership in a named reel;
ordinary personal APIs retain owner isolation. Only successful, current final
results are shared. Artifact access is revalidated instead of cached immutably.

See [API routes](api.md), [database](database.md), and [testing](testing.md).
