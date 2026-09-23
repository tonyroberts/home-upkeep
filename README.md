# Home Upkeep - A Task Tracker for Home Assistant

Home Upkeep is a local 'to-do' list for recurring and non-recurring household tasks, such as cleaning, gardening and maintenance chores.

It is not a replacement for a calendar and is not intended to alert you if a task needs doing on a specific day or time (i.e. it is not a bin day reminder!).

Instead, it is useful for tracking tasks that need doing _when you have time_.

For example, I use it to track things that need doing. When I wake up on a weekend with some free time, I can check what needs doing around the house and in the garden.

If you find it useful, please consider buying me a coffee.

<a href="https://www.buymeacoffee.com/tonyroberts" target="_blank"><img src="https://cdn.buymeacoffee.com/buttons/v2/default-yellow.png" alt="Buy Me A Coffee" height="50px" width="210px"></a>

## About

Home Upkeep is designed around how household tasks actually work in real life. Unlike traditional to-do lists that rigidly schedule recurring tasks, Home Upkeep adapts to your actual completion patterns.

### Recurring Tasks

Most task management systems schedule recurring tasks with fixed intervals, but Home Upkeep understands that life doesn't always follow a strict schedule. When you complete a task, by default, the next occurrence is scheduled from that completion date, not the original due date.

**Example:** A cleaning task scheduled every 4 weeks. If you complete it a week late, the next task will be scheduled 4 weeks from when you actually finished it, not from the original due date.

For tasks that must maintain a fixed schedule regardless of completion timing, you can choose to reschedule from the due date instead.

### Seasonal Tasks

Gardening and outdoor tasks often have seasonal constraints. Home Upkeep allows you to specify which months tasks can or cannot be completed in.

**Example:** Pruning fruit trees is an annual task that must be completed between December and February. Set it up as a yearly recurring task, and if it slips into February, you'll see a warning telling you it must be done before March.

Seasonal constraints also apply to recurring tasks. For instance, weed spraying in the UK typically only happens between April and October. A monthly spraying task will automatically skip the winter months and resume in April.

### Constraints

Add custom constraints and notes to tasks to inform you of specific conditions (e.g., "apply lawn feed only when rain is forecast"). These constraints help you prioritize tasks and make informed decisions about when to complete them.

You can snooze tasks if conditions aren't right, ensuring you see them again when it's more appropriate to tackle them.

## Screenshots

<img src="https://github.com/tonyroberts/home-upkeep-addon/blob/main/home-upkeep/screenshot.png?raw=true" width="500">

### Installation

Home Upkeep now runs entirely as this Home Assistant custom integration — no
add-on, no separate container. It ships its own sidebar panel (a Home
Assistant custom panel) and needs nothing else installed.

The integration can be installed via HACS:

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://img.shields.io/badge/HACS-Custom-orange.svg?style=for-the-badge)](https://my.home-assistant.io/redirect/hacs_repository/?owner=tonyroberts&repository=home-upkeep&category=integration)

Or to install manually:
   - Add the repository [https://github.com/tonyroberts/home-upkeep](https://github.com/tonyroberts/home-upkeep) to your HACS custom repositories, using type 'integration'
   - Install the Home Upkeep custom integration
   - Add the integration under Settings -> Devices & services

#### Upgrading from a version that used the add-on

Earlier versions of this integration only exposed your add-on's task lists as
`todo` entities and required the separate Home Upkeep **add-on** to be
installed and running. If you're upgrading from one of those versions,
updating this integration via HACS is all you need to do: on its next
restart, it automatically pulls your existing lists and tasks from the add-on
into its own storage, once, and the new panel appears in the sidebar with
that data already in it.

Once the migration has run, a red banner appears in the panel letting you
know your data has moved over and it's safe to uninstall the Home Upkeep
add-on.

