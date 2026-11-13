/* Mobigen console: the agent registry. The shell (shell.js) fills the agent switcher from this list and
   loads the chosen agent's markup, style and script from the same origin (strict CSP: script-src 'self').
   One entry per agent screen set under agents/<id>/:
     id      short name; also the ?agent= query value and the stored choice
     title   the label in the switcher and the page title
     base    the agent's HTTP API prefix (the screens talk to the agent only through it)
     markup  the screen's HTML fragment (elements with data-slot="topbar" go to the shared top bar)
     helper  optional: a DOM-free helper script loaded just before the screen's own script
     extras  optional: more files of the screen set, from this or another agent's folder (a .css file is a stylesheet,
             anything else a script). They load after the helper and BEFORE the screen's own script, in the order
             listed, so the screen can use what they expose at boot; an extra never depends on the screen's script
     script  the screen's code (vanilla JS, no build step)
     style   the screen's styles */
(function (root) {
  "use strict";
  root.MobigenRegistry = [
    {
      id: "compass",
      title: "Compass",
      base: "/v1/compass",
      markup: "agents/compass/compass.html",
      helper: "agents/compass/interview.js",
      extras: ["agents/research/research_view.js", "agents/research/research.js", "agents/research/research.css"],
      script: "agents/compass/compass.js",
      style: "agents/compass/compass.css"
    }
  ];
})(window);
