"""A single answer leaves one chip on a multi picker (Twitch, 2026-10-21)."""

from jobpilot.widgets import combobox


async def test_a_single_answer_multi_picker_keeps_only_that_chip(page):
    # Twitch's react-select citizenship menu held "Laos" and "Latvia" from earlier passes; fixing the value added a third.
    await page.set_content("""<div class=select__control id=owner>
      <div class=select__value-container--is-multi>
        <div class=select__multi-value><div class=select__multi-value__label>Laos</div>
          <div class=select__multi-value__remove onclick="this.parentElement.remove()">x</div></div>
        <div class=select__multi-value><div class=select__multi-value__label>Latvia</div>
          <div class=select__multi-value__remove onclick="this.parentElement.remove()">x</div></div>
      </div>
      <input id=source class=select__input role=combobox aria-controls=menu aria-expanded=false
        onclick="this.setAttribute('aria-expanded','true');document.querySelector('#menu').hidden=false">
      </div><div id=menu role=listbox hidden><div role=option onclick="
        const input=document.querySelector('#source');input.value='';input.setAttribute('aria-expanded','false');
        document.querySelector('.select__value-container--is-multi').insertAdjacentHTML('beforeend','<div class=select__multi-value><div class=select__multi-value__label>Freedonia</div></div>');
        this.parentElement.hidden=true;
      ">Freedonia</div></div>""")
    assert await combobox(page.main_frame, page.locator("#source"), "Freedonia") == "Freedonia"
    assert await page.locator(".select__multi-value").count() == 1
