using System;
using System.Threading.Tasks;

namespace Coupons.Auth
{
    public interface IPettable { void Pet(); }

    public abstract class Animal
    {
        public abstract string Speak();
    }

    public class Dog : Animal, IPettable
    {
        private string _name;

        public Dog(string name) { _name = name; }

        public override string Speak()
        {
            Helper.Log(_name);
            return BarkStyle();
        }

        private string BarkStyle()
        {
            return "woof";
        }

        public void Pet() { }
    }

    public enum Kind { Good, Bad }

    public static class Runner
    {
        public static void Run()
        {
            var d = new Dog("rex");
            d.Speak();
            System.Console.WriteLine("hi");
        }
    }
}
