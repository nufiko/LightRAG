// C# 10+ file-scoped namespace form.
using System;

namespace Coupons.Billing;

public class Invoice
{
    public int Id { get; set; }

    public string Render()
    {
        return $"Invoice #{Id}";
    }
}
