from django import template
from django.utils.html import format_html

register = template.Library()


def _sort_state(context, field, default_field, default_direction):
    request = context["request"]
    current = request.GET.get("sort") or default_field
    direction = request.GET.get("dir", default_direction)
    if direction not in {"asc", "desc"}:
        direction = "asc"
    active = current == field
    next_direction = "desc" if active and direction == "asc" else "asc"
    query = request.GET.copy()
    query.pop("page", None)
    query["sort"] = field
    query["dir"] = next_direction
    return active, direction, query.urlencode()


def _sort_indicator(state):
    if state == "asc":
        path = '<path d="M4 10l4-4 4 4" />'
    elif state == "desc":
        path = '<path d="M4 6l4 4 4-4" />'
    else:
        path = '<path d="M4.5 6.5L8 3l3.5 3.5M4.5 9.5L8 13l3.5-3.5" />'
    return format_html(
        '<span class="sort-indicator state-{}" aria-hidden="true">'
        '<svg viewBox="0 0 16 16" focusable="false">{}</svg>'
        '</span>',
        state,
        format_html(path),
    )


@register.simple_tag(takes_context=True)
def sort_header(context, field, label, default_field="", default_direction="asc"):
    active, direction, query = _sort_state(context, field, default_field, default_direction)
    state = "asc" if active and direction == "asc" else "desc" if active else "none"
    action = (
        "сортировать по убыванию"
        if active and direction == "asc"
        else "сортировать по возрастанию"
    )
    css = "sortable-link active" if active else "sortable-link"
    return format_html(
        '<a class="{}" href="?{}" aria-label="{}: {}">'
        '<span class="sort-label">{}</span>{}</a>',
        css,
        query,
        label,
        action,
        label,
        _sort_indicator(state),
    )


@register.simple_tag(takes_context=True)
def sort_chip(context, field, label, default_field="", default_direction="asc"):
    active, direction, query = _sort_state(context, field, default_field, default_direction)
    state = "asc" if active and direction == "asc" else "desc" if active else "none"
    action = (
        "сортировать по убыванию"
        if active and direction == "asc"
        else "сортировать по возрастанию"
    )
    css = "sort-chip active" if active else "sort-chip"
    return format_html(
        '<a class="{}" href="?{}" aria-label="{}: {}"><span>{}</span>{}</a>',
        css,
        query,
        label,
        action,
        label,
        _sort_indicator(state),
    )
