from django.urls import include, path

urlpatterns = [
    path("", include("calculators.urls")),
]
