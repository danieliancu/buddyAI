/* The "How it works" simulator: one tab per part of the watch. Each step drives the simulated screen
 * through `run` (an action of src/scripts/how-sim.ts) and can point at a side button (`hot`).
 * The wording follows what the firmware and the assistant really do. */

export type Hot = "talk" | "power";

export interface Step {
  title: string;
  text: string;
  /** Things to say to the watch. */
  say?: string[];
  /** The touch way to do it. */
  touch?: string;
  run: string;
  hot?: Hot;
}

export interface Tab {
  id: string;
  label: string;
  title: string;
  lead: string;
  steps: Step[];
}

export const tabs = (trialDays: number): Tab[] => [
  {
    id: "onboarding",
    label: "Onboarding",
    title: "From the box to your first conversation",
    lead: "About five minutes, no app to install. You only need your phone and your Wi-Fi password.",
    steps: [
      {
        title: "Order and set your password",
        text: `Pay with secure Stripe checkout. We create your olá account with your checkout email and send you a link to set its password. Your free ${trialDays}-day olácare trial starts only when you pair the watch.`,
        run: "off",
      },
      {
        title: "Turn it on",
        text: "Hold the bottom button for about a second. A new watch opens on its Wi-Fi setup screen.",
        touch: "Hold the bottom button",
        run: "setup:on",
        hot: "power",
      },
      {
        title: "Wi-Fi on Android",
        text: "Open your olá account in Chrome and tap “Connect to watch”. Type the 8-character password the watch shows, then choose the Wi-Fi you prefer, your home network or your phone's hotspot, and enter its password. (If this doesn't work, use the iPhone way below: it works on Android too.)",
        touch: "Android phone, Chrome",
        run: "setup:android",
      },
      {
        title: "Wi-Fi on iPhone: join the watch",
        text: "Scan the code on the watch with the Camera, or in Settings › Wi-Fi join the network whose name starts with “ola-” (for example ola-1A2B), with the password shown on the watch. A page opens: choose your home Wi-Fi (2.4 GHz), enter its password and tap Save. Then, in your olá account, tap “The watch is on my Wi-Fi”.",
        touch: "iPhone, Camera",
        run: "setup:iphone",
      },
      {
        title: "Pair it with your account",
        text: "Android or iPhone, the watch then restarts on your Wi-Fi and shows a 6-digit code. Type it in your olá account (Add watch) within 5 minutes.",
        run: "setup:pair",
      },
      {
        title: "Start talking",
        text: "That's it. Press the top button and say hello.",
        touch: "Press the top button",
        run: "setup:done",
        hot: "talk",
      },
    ],
  },
  {
    id: "home",
    label: "Home",
    title: "Your watch at a glance",
    lead: "Everything starts on the watchface: the time, a big mic to talk, and three shortcuts.",
    steps: [
      {
        title: "The watchface",
        text: "Time and date in the middle, Wi-Fi and battery at the top. When the screen is dark, the first tap only wakes it.",
        run: "home",
      },
      {
        title: "The mic: talk to olá",
        text: "Tap the big mic, or press the top button on the side, and just speak. No wake word.",
        touch: "Tap the mic or press the top button",
        run: "hl:mic",
        hot: "talk",
      },
      {
        title: "Notes, Reminders, Settings",
        text: "The three round shortcuts. The red badges count your notes and the reminders still to come today and after.",
        touch: "Tap a shortcut",
        run: "hl:shortcuts",
      },
      {
        title: "Wake and sleep",
        text: "Press the bottom button to wake the screen. It goes to sleep after the screen timeout, showing a quiet analogue clock if you turned that on in Settings.",
        touch: "Short press on the bottom button",
        run: "standby",
        hot: "power",
      },
      {
        title: "Turn it off and on",
        text: "Hold the bottom button for 2 seconds to turn the watch off. Hold it for about a second to turn it back on.",
        touch: "Hold the bottom button",
        run: "poweroff",
        hot: "power",
      },
    ],
  },
  {
    id: "talk",
    label: "Talk",
    title: "Just talk to it",
    lead: "One press and you are in a conversation. olá answers out loud, and the words appear on the screen as it speaks.",
    steps: [
      {
        title: "Press the top button",
        text: "Or tap the mic on the watchface. The watch starts listening right away, from any screen, even when it is asleep.",
        touch: "Top button, or tap the mic",
        run: "listen",
        hot: "talk",
      },
      {
        title: "Ask anything",
        text: "Speak naturally. The dots show that olá is listening; it knows when you have finished.",
        say: ["What's the weather in London tomorrow?"],
        run: "chat:weather",
      },
      {
        title: "Quick answers, big and clear",
        text: "Numbers, times and temperatures show large on the screen, so you can read them at a glance.",
        say: ["What's 15% of 80?"],
        run: "chat:math",
      },
      {
        title: "It asks, you answer",
        text: "If olá needs something from you, the mic opens again by itself. Just answer.",
        say: ["Remind me to buy flowers", "At six"],
        run: "chat:followup",
      },
      {
        title: "Interrupt or finish",
        text: "Press the button while olá is speaking to cut in with a new question. Tap the X or swipe right to end the conversation.",
        touch: "Top button to interrupt, X to finish",
        run: "chat:end",
        hot: "talk",
      },
    ],
  },
  {
    id: "notes",
    label: "Notes",
    title: "Notes you can say",
    lead: "Dictate a note, read it on your wrist, change it line by line with your voice, and send it to your email with a word or a tap.",
    steps: [
      {
        title: "Dictate a note",
        text: "Say what to note. The first thing you say becomes its title; lists become numbered lines.",
        say: ["Note: shopping list, milk, eggs and coffee"],
        run: "notes:dictate",
      },
      {
        title: "Open your notes",
        text: "Tap the pen shortcut, or ask. Pinned notes stay at the top.",
        say: ["Show my notes"],
        touch: "Tap the pen on the watchface",
        run: "notes:list",
      },
      {
        title: "Read a note",
        text: "Tap a note to open it: the title, then its numbered lines.",
        touch: "Tap a note",
        run: "notes:open",
      },
      {
        title: "Edit it by voice",
        text: "Tap the mic on the note and say a line to add it, or a change. The line that changed flashes.",
        say: ["Add bread", "Delete 2", "Move 3 to the top"],
        run: "notes:edit",
      },
      {
        title: "Pin or delete",
        text: "Pin keeps a note first. Delete asks for a second tap; by voice, olá asks you to confirm before anything is deleted.",
        say: ["Delete the shopping list"],
        touch: "Pin and trash buttons under the note",
        run: "notes:pin",
      },
      {
        title: "Send it to your email",
        text: "Ask, or tap the envelope next to a note. olá sends it to the email address of your olá account.",
        say: ["Send my shopping list to my email"],
        touch: "Tap the envelope on a note",
        run: "notes:email",
      },
      {
        title: "In your account too",
        text: "Every note is also in your olá account, where you can type, edit or delete long notes from your phone or computer.",
        run: "notes:list",
      },
    ],
  },
  {
    id: "reminders",
    label: "Reminders",
    title: "Reminders, by voice",
    lead: "Say when and what. The watch keeps your day in order and wakes up when it's time.",
    steps: [
      {
        title: "Ask for a reminder",
        text: "Say the time and what to remind you of. Add a place, people, or an alert in advance if you like.",
        say: ["Remind me tomorrow at 9 to take the car to the garage"],
        run: "rem:dictate",
      },
      {
        title: "See your day",
        text: "Tap the calendar shortcut, or ask. Today is framed at the top, then the next days.",
        say: ["What are my reminders today?"],
        touch: "Tap the calendar on the watchface",
        run: "rem:list",
      },
      {
        title: "All the details",
        text: "Open a reminder for its time, place, description, advance alert and who is coming.",
        touch: "Tap a reminder",
        run: "rem:open",
      },
      {
        title: "Change it by voice",
        text: "Tap the mic on the reminder and say what changes.",
        say: ["At 15:30", "Add Ana", "Remind me 15 minutes before"],
        run: "rem:edit",
      },
      {
        title: "Mark it done",
        text: "Tap the check when it's done. Completed reminders move to the end of their day.",
        say: ["I've done it"],
        touch: "Tap the check",
        run: "rem:done",
      },
      {
        title: "When it's time",
        text: "The watch wakes up, beeps and shows the reminder, even from standby.",
        run: "rem:alert",
      },
    ],
  },
  {
    id: "settings",
    label: "Settings",
    title: "Make it yours",
    lead: "Two pages of settings on the watch, all of them live here: try the switches and drag the bars.",
    steps: [
      {
        title: "Open settings",
        text: "Tap the gear on the watchface, or swipe left on it.",
        touch: "Gear, or swipe left",
        run: "set:open",
      },
      {
        title: "Theme: blue or white",
        text: "Flip the switch to change the colours of the whole watch.",
        say: ["Make it white"],
        run: "set:theme",
      },
      {
        title: "Brightness and volume",
        text: "Drag across the blocks. Or just say it.",
        say: ["A bit brighter", "Volume to maximum", "Quieter"],
        run: "set:levels",
      },
      {
        title: "Screen timeout",
        text: "Tap to choose how long the screen stays on: 10 seconds to 5 minutes.",
        touch: "Tap the row",
        run: "set:timeout",
      },
      {
        title: "Page 2: standby clock, language, Wi-Fi",
        text: "Swipe left or tap the arrow. Turn on the clock in standby, pick the language olá speaks, or open Wi-Fi setup.",
        say: ["Switch to Romanian", "Turn on the clock in standby"],
        touch: "Swipe left or tap ›",
        run: "set:page2",
      },
      {
        title: "Wi-Fi: your networks",
        text: "Tap Wi-Fi setup on page 2. At the top, the network the watch is on; below, the other networks it remembers (up to 5 in all). Tap one to switch to it, tap its bin twice to forget it. The watch also joins the strongest saved network in range by itself, so going from home to the office or to your phone's hotspot needs nothing from you.",
        touch: "Page 2 › Wi-Fi setup",
        run: "set:wifi",
      },
      {
        title: "Wi-Fi: a new network",
        text: "Under Nearby, tap a network. If it has a password, type it on the watch's keyboard and tap OK. The watch saves it only once it connects, so a mistyped password changes nothing. Prefer your phone? Tap Set up with phone and type the password there, as in Onboarding.",
        touch: "Nearby › a network",
        run: "set:wifi-add",
      },
      {
        title: "More in your account",
        text: "Persona, voice, speaking speed and time zone are in your olá account, along with your conversation history.",
        run: "set:page1",
      },
    ],
  },
];
