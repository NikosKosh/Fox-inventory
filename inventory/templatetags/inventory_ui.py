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


@register.simple_tag(takes_context=True)
def sort_header(context, field, label, default_field="", default_direction="asc"):
    active, direction, query = _sort_state(context, field, default_field, default_direction)
    state = "asc" if active and direction == "asc" else "desc" if active else "none"
    title = (
        "Сортировать по убыванию"
        if active and direction == "asc"
        else "Сортировать по возрастанию"
    )
    css = "sortable-link active" if active else "sortable-link"
    aria_sort = "ascending" if active and direction == "asc" else "descending" if active else "none"
    return format_html(
        '<a class="{}" href="?{}" title="{}" aria-label="{}; текущая сортировка: {}">'
        '<span class="sort-label">{}</span><span class="sort-arrow state-{}" aria-hidden="true"></span></a>',
        css,
        query,
        title,
        label,
        aria_sort,
        label,
        state,
    )


@register.simple_tag(takes_context=True)
def sort_chip(context, field, label, default_field="", default_direction="asc"):
    active, direction, query = _sort_state(context, field, default_field, default_direction)
    indicator = "↑" if active and direction == "asc" else "↓" if active else "↕"
    css = "sort-chip active" if active else "sort-chip"
    return format_html(
        '<a class="{}" href="?{}"><span>{}</span><span aria-hidden="true">{}</span></a>',
        css,
        query,
        label,
        indicator,
    )

@register.filter
def money(value):
    from inventory.catalog import format_money
    return format_money(value)
