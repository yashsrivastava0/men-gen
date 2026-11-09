/* Mobigen console: the agent registry. The shell (shell.js) fills the agent switcher from this list and
   loads the chosen agent's markup, style and script from the same origin (strict CSP: script-src 'self').
   One entry per agent screen set under agents/<id>/:
     id      short name; also the ?agent= query value and the stored choice
     title   the label in the switcher and the page title
     base    the agent's HTTP API prefix (the screens talk to the agent only through it)
     markup  the screen's HTML fragment (elements with data-slot="topbar" go to the shared top bar)
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
      script: "agents/compass/compass.js",
      style: "agents/compass/compass.css"
    }
  ];
})(window);
