"""TVLens auth forms."""

from django import forms
from django.conf import settings
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.models import User
from django.db import transaction

from .models import InviteUse


class RegistrationForm(UserCreationForm):
    email = forms.EmailField(
        required=True,
        help_text="Required. Used for password reset.",
    )
    # The beta is invite-only: anyone may browse, but an account needs a code,
    # and each code signs up one person (see InviteUse).
    invite_code = forms.CharField(max_length=64, label="Invite code")

    # The code first: without one, nothing else on the form matters.
    field_order = ["invite_code", "username", "email", "password1", "password2"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # UserCreationForm puts the cursor in username; it belongs in the first box.
        self.fields["username"].widget.attrs.pop("autofocus", None)
        self.fields["invite_code"].widget.attrs["autofocus"] = True

    class Meta(UserCreationForm.Meta):
        model = User
        fields = ("username", "email", "password1", "password2")

    def clean_invite_code(self):
        code = self.cleaned_data["invite_code"].strip()
        # One message for a wrong code and a spent one, so the form never
        # confirms which codes exist.
        if code not in settings.BETA_INVITE_CODES or InviteUse.objects.filter(code=code).exists():
            raise forms.ValidationError("That invite code isn't valid.")
        return code

    def save(self, commit=True):
        user = super().save(commit=False)
        user.email = self.cleaned_data["email"]
        if commit:
            # The user and the spent code land together or not at all. The
            # unique code column settles two sign-ups racing for one code.
            with transaction.atomic():
                user.save()
                InviteUse.objects.create(code=self.cleaned_data["invite_code"], user=user)
        return user
