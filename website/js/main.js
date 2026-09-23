/*
  Aura Retail marketing site -- interaction glue (mobile nav, contact links,
  contact form). Plain JS, no dependencies, matches the rest of the site's
  no-build-step rule.
*/
(function () {
  "use strict";

  function initMobileNav() {
    var header = document.querySelector(".site-header");
    var toggle = document.querySelector(".nav-toggle");
    if (!header || !toggle) return;
    toggle.addEventListener("click", function () {
      var isOpen = header.classList.toggle("nav-open");
      toggle.setAttribute("aria-expanded", isOpen ? "true" : "false");
    });
    // Close the mobile panel when a link inside it is followed.
    header.querySelectorAll(".mobile-panel a").forEach(function (link) {
      link.addEventListener("click", function () {
        header.classList.remove("nav-open");
        toggle.setAttribute("aria-expanded", "false");
      });
    });
  }

  // Populate every element carrying data-contact-link="<key>" from the
  // single source of truth in js/content.js's AURA_CONTACT object -- see
  // that file's comment above CONTACT and website/README.md's "The
  // contact placeholders". This is what keeps the footer's mailto: link
  // (repeated on all four pages) and contact.html's own "reach us
  // directly" panel in sync with one edit, instead of several hardcoded
  // copies quietly drifting apart. A falsy value (phone is empty until a
  // real one exists) removes the element instead of showing a blank or
  // fake link -- see CONTACT's own comment for why.
  function initContactLinks() {
    var contact = window.AURA_CONTACT;
    if (!contact) return;
    document.querySelectorAll("[data-contact-link]").forEach(function (el) {
      var key = el.getAttribute("data-contact-link");
      var value = contact[key];
      if (!value) { el.remove(); return; }
      if (key === "email") {
        el.href = "mailto:" + value;
        el.textContent = value;
      } else if (key === "phone") {
        el.href = "tel:" + value.replace(/\s+/g, "");
        el.textContent = value;
      }
    });
  }

  // Honest contact form: this is a static site with no backend, so
  // "submitting" builds a mailto: link from what was typed and hands the
  // browser to the visitor's own email client. Nothing is uploaded and
  // nothing claims to have been "sent" -- see the form_note copy in
  // js/content.js and website/README.md's "no fake success state" note.
  function initContactForm() {
    var form = document.getElementById("contact-form");
    if (!form) return;
    form.addEventListener("submit", function (evt) {
      evt.preventDefault();
      var lang = document.documentElement.lang === "ar" ? "ar" : "en";
      var dict = window.AURA_CONTENT[lang].contact_page;
      var contact = window.AURA_CONTACT;

      var name = (form.querySelector("#f-name") || {}).value || "";
      var shop = (form.querySelector("#f-shop") || {}).value || "";
      var city = (form.querySelector("#f-city") || {}).value || "";
      var phone = (form.querySelector("#f-phone") || {}).value || "";
      var email = (form.querySelector("#f-email") || {}).value || "";
      var message = (form.querySelector("#f-message") || {}).value || "";

      var bodyLines = [
        dict.form_name + ": " + name,
        dict.form_shop + ": " + shop,
        dict.form_city + ": " + city,
        dict.form_phone + ": " + phone,
        dict.form_email + ": " + email,
        "",
        message
      ];

      var mailto =
        "mailto:" + encodeURIComponent(contact.email) +
        "?subject=" + encodeURIComponent(dict.subject + (shop ? " -- " + shop : "")) +
        "&body=" + encodeURIComponent(bodyLines.join("\n"));

      window.location.href = mailto;
    });
  }

  function init() {
    initMobileNav();
    initContactLinks();
    initContactForm();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
