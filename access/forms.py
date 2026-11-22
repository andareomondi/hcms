from django import forms


class EnrolForm(forms.Form):
    username = forms.CharField(max_length=150)
    password = forms.CharField(widget=forms.PasswordInput, strip=False)


class CodeForm(forms.Form):
    code = forms.CharField(max_length=64)
