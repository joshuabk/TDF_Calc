from django.urls import path

from . import views

urlpatterns = [
    path("", views.home, name="home"),
    path("bed/", views.bed_view, name="bed"),
    path("bed-eqd2/", views.bed_eqd2_view, name="bed_eqd2"),
    path("tdf/", views.tdf_view, name="tdf"),
    path("reference/", views.reference_view, name="reference"),
    path("rule-of-thumb/", views.rule_of_thumb_view, name="rule_of_thumb"),
]
